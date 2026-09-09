"""V9 PostgreSQL-backed Supabase replacement surface.

Uses PostgreSQL for durable data and sessions, RLS for tenant isolation,
LISTEN/NOTIFY for realtime events, and a database-backed job queue. The API
never executes user supplied function code inside the web process.
"""
import hashlib, hmac, os, secrets, time, uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None

from auth import issue_token, verify_token

router = APIRouter(prefix="/supabase/v9", tags=["supabase-v9"])
PG_DSN = os.getenv("POSTGRES_DSN", "postgresql://supabase:change-me@postgres:5432/supabase")
REFRESH_TTL = max(3600, int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", str(60 * 60 * 24 * 30))))


def db():
    if psycopg is None:
        raise HTTPException(503, "PostgreSQL driver is not installed")
    try:
        return psycopg.connect(PG_DSN, row_factory=dict_row)
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL is unavailable") from exc


def user(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    u = verify_token(authorization[7:].strip())
    if not u:
        raise HTTPException(401, "Invalid or expired access token")
    return u


def set_rls(conn, uid: str):
    conn.execute("SELECT set_config('app.user_id', %s, false)", (uid,))


def refresh_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RowBody(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)

class AuthRefresh(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=500)

class JobBody(BaseModel):
    job_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
def status():
    with db() as conn:
        conn.execute("SELECT 1")
    return {"version": "9.0.0", "database": "postgresql", "rls": True, "realtime": "postgres-notify", "jobs": True}


@router.post("/auth/refresh")
def refresh(body: AuthRefresh):
    token_hash = refresh_hash(body.refresh_token)
    with db() as conn:
        row = conn.execute("SELECT u.* FROM sb_sessions s JOIN sb_users u ON u.id=s.user_id WHERE s.refresh_token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>now()", (token_hash,)).fetchone()
        if not row:
            raise HTTPException(401, "Invalid or expired refresh token")
        new_refresh = secrets.token_urlsafe(48)
        conn.execute("UPDATE sb_sessions SET revoked_at=now() WHERE refresh_token_hash=%s", (token_hash,))
        conn.execute("INSERT INTO sb_sessions(user_id,refresh_token_hash,expires_at) VALUES(%s,%s,now()+make_interval(secs => %s))", (row["id"], refresh_hash(new_refresh), REFRESH_TTL))
        conn.commit()
    access = issue_token({"id": str(row["id"]), "email": row["email"], "name": row["name"]})
    return {"access_token": access, "refresh_token": new_refresh, "token_type": "bearer"}


@router.post("/auth/revoke")
def revoke(authorization: str | None = Header(default=None)):
    u = user(authorization)
    with db() as conn:
        conn.execute("UPDATE sb_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (u["id"],))
        conn.commit()
    return {"revoked": True}


@router.get("/rest/{table}")
def select(table: str, authorization: str | None = Header(default=None), limit: int = 100, offset: int = 0, order: str = "created_at.desc"):
    u = user(authorization); _validate_table(table)
    limit, offset = min(max(limit, 1), 1000), max(offset, 0)
    column, direction = _order(order)
    with db() as conn:
        set_rls(conn, u["id"])
        rows = conn.execute(f"SELECT id, row_data, created_at, updated_at FROM sb_data WHERE table_name=%s ORDER BY {column} {direction} LIMIT %s OFFSET %s", (table, limit, offset)).fetchall()
    return {"data": [{"id": str(r["id"]), **r["row_data"], "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()} for r in rows]}


@router.post("/rest/{table}")
def insert(table: str, body: RowBody, authorization: str | None = Header(default=None)):
    u = user(authorization); _validate_table(table)
    with db() as conn:
        set_rls(conn, u["id"])
        row = conn.execute("INSERT INTO sb_data(owner_id,table_name,row_data) VALUES(%s,%s,%s) RETURNING id,created_at,updated_at", (u["id"], table, psycopg.types.json.Jsonb(body.data))).fetchone()
        conn.commit()
    return {"data": {"id": str(row["id"]), **body.data, "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}}


@router.patch("/rest/{table}/{row_id}")
def update(table: str, row_id: str, body: RowBody, authorization: str | None = Header(default=None)):
    u = user(authorization); _validate_table(table)
    with db() as conn:
        set_rls(conn, u["id"])
        row = conn.execute("UPDATE sb_data SET row_data=row_data || %s::jsonb WHERE id=%s AND table_name=%s RETURNING id,row_data,updated_at", (psycopg.types.json.Jsonb(body.data), row_id, table)).fetchone()
        if not row: raise HTTPException(404, "Row not found")
        conn.commit()
    return {"data": {"id": str(row["id"]), **row["row_data"], "updated_at": row["updated_at"].isoformat()}}


@router.delete("/rest/{table}/{row_id}")
def delete(table: str, row_id: str, authorization: str | None = Header(default=None)):
    u = user(authorization); _validate_table(table)
    with db() as conn:
        set_rls(conn, u["id"])
        cur = conn.execute("DELETE FROM sb_data WHERE id=%s AND table_name=%s", (row_id, table)); conn.commit()
    if cur.rowcount == 0: raise HTTPException(404, "Row not found")
    return {"deleted": True}


@router.post("/jobs")
def enqueue(body: JobBody, authorization: str | None = Header(default=None)):
    u = user(authorization)
    with db() as conn:
        row = conn.execute("INSERT INTO sb_jobs(user_id,job_type,payload) VALUES(%s,%s,%s) RETURNING id,status,created_at", (u["id"], body.job_type, psycopg.types.json.Jsonb(body.payload))).fetchone(); conn.commit()
    return {"id": str(row["id"]), "status": row["status"], "created_at": row["created_at"].isoformat()}


@router.get("/jobs/{job_id}")
def job(job_id: str, authorization: str | None = Header(default=None)):
    u = user(authorization)
    with db() as conn:
        row = conn.execute("SELECT id,job_type,status,attempts,result,error,created_at,updated_at FROM sb_jobs WHERE id=%s AND user_id=%s", (job_id, u["id"])).fetchone()
    if not row: raise HTTPException(404, "Job not found")
    return {**{k: row[k] for k in ("job_type","status","attempts","result","error")}, "id": str(row["id"]), "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}


def _validate_table(table: str):
    if not table or len(table) > 100 or not table.replace("_", "a").isalnum():
        raise HTTPException(400, "Invalid table name")

def _order(value: str):
    parts = value.split(".", 1)
    column = parts[0] if parts and parts[0] in {"created_at", "updated_at"} else "created_at"
    direction = "DESC" if len(parts) == 1 or parts[1].lower() == "desc" else "ASC"
    return column, direction
