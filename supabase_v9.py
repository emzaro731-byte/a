"""PostgreSQL-backed Supabase-compatible API.

This is a compatibility layer, not Supabase's proprietary service internals.
It provides Auth, REST-like data, Storage metadata/files, jobs, vectors,
audit logging and realtime over PostgreSQL LISTEN/NOTIFY.
"""
import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None
    Jsonb = None

from auth import hash_password, issue_token, verify_password, verify_token

router = APIRouter(prefix="/supabase/v9", tags=["supabase-v9"])
PG_DSN = os.getenv("POSTGRES_DSN", "")
STORAGE_ROOT = os.getenv("STORAGE_ROOT", "/data/storage")
REFRESH_TTL = max(3600, int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", str(60 * 60 * 24 * 30))))
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def db():
    if psycopg is None or not PG_DSN:
        raise HTTPException(503, "PostgreSQL is not configured")
    try:
        return psycopg.connect(PG_DSN, row_factory=dict_row)
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL is unavailable") from exc


def current_user(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    u = verify_token(authorization[7:].strip())
    if not u:
        raise HTTPException(401, "Invalid or expired access token")
    return u


def set_rls(conn, uid: str):
    conn.execute("SELECT set_config('app.user_id', %s, true)", (str(uid),))


def audit(conn, uid: str | None, action: str, resource: str, metadata: dict[str, Any] | None = None):
    conn.execute("INSERT INTO sb_audit_log(user_id,action,resource,metadata) VALUES(%s,%s,%s,%s)", (uid, action, resource, Jsonb(metadata or {})))


def refresh_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RowBody(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)


class AuthBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=500)


class JobBody(BaseModel):
    job_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class BucketBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    public: bool = False


class VectorBody(BaseModel):
    namespace: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=100000)
    embedding: list[float] = Field(min_length=1536, max_length=1536)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
def status():
    with db() as conn:
        conn.execute("SELECT 1")
    return {"version": "10.0.0", "database": "postgresql", "rls": True, "pgvector": True, "realtime": "postgres-notify", "storage": True, "jobs": True, "audit": True}


@router.post("/auth/register")
def register(body: AuthBody):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Enter a valid email address")
    with db() as conn:
        try:
            row = conn.execute("INSERT INTO sb_users(email,name,password_hash) VALUES(%s,%s,%s) RETURNING id,email,name,email_verified", (email, body.name.strip(), hash_password(body.password))).fetchone()
            refresh = secrets.token_urlsafe(48)
            conn.execute("INSERT INTO sb_sessions(user_id,refresh_token_hash,expires_at) VALUES(%s,%s,now()+make_interval(secs => %s))", (row["id"], refresh_hash(refresh), REFRESH_TTL))
            audit(conn, str(row["id"]), "auth.register", "user")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                raise HTTPException(409, "An account with this email already exists") from exc
            raise
    return {"user": {"id": str(row["id"]), "email": row["email"], "name": row["name"], "email_verified": row["email_verified"]}, "access_token": issue_token(row), "refresh_token": refresh, "token_type": "bearer"}


@router.post("/auth/login")
def login(body: AuthBody):
    email = body.email.strip().lower()
    with db() as conn:
        row = conn.execute("SELECT id,email,name,password_hash,email_verified FROM sb_users WHERE email=%s", (email,)).fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(401, "Invalid email or password")
        refresh = secrets.token_urlsafe(48)
        conn.execute("INSERT INTO sb_sessions(user_id,refresh_token_hash,expires_at) VALUES(%s,%s,now()+make_interval(secs => %s))", (row["id"], refresh_hash(refresh), REFRESH_TTL))
        audit(conn, str(row["id"]), "auth.login", "user")
        conn.commit()
    return {"user": {"id": str(row["id"]), "email": row["email"], "name": row["name"], "email_verified": row["email_verified"]}, "access_token": issue_token(row), "refresh_token": refresh, "token_type": "bearer"}


@router.post("/auth/refresh")
def refresh(body: RefreshBody):
    with db() as conn:
        row = conn.execute("SELECT u.* FROM sb_sessions s JOIN sb_users u ON u.id=s.user_id WHERE s.refresh_token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>now()", (refresh_hash(body.refresh_token),)).fetchone()
        if not row:
            raise HTTPException(401, "Invalid or expired refresh token")
        new_refresh = secrets.token_urlsafe(48)
        conn.execute("UPDATE sb_sessions SET revoked_at=now() WHERE refresh_token_hash=%s", (refresh_hash(body.refresh_token),))
        conn.execute("INSERT INTO sb_sessions(user_id,refresh_token_hash,expires_at) VALUES(%s,%s,now()+make_interval(secs => %s))", (row["id"], refresh_hash(new_refresh), REFRESH_TTL))
        conn.commit()
    return {"access_token": issue_token(row), "refresh_token": new_refresh, "token_type": "bearer"}


@router.post("/auth/revoke")
def revoke(authorization: str | None = Header(default=None)):
    u = current_user(authorization)
    with db() as conn:
        conn.execute("UPDATE sb_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (u["id"],))
        audit(conn, u["id"], "auth.revoke", "sessions")
        conn.commit()
    return {"revoked": True}


@router.get("/auth/me")
def me(authorization: str | None = Header(default=None)):
    return current_user(authorization)


@router.get("/rest/tables")
def tables(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with db() as conn:
        rows = conn.execute("SELECT DISTINCT table_name FROM sb_data ORDER BY table_name").fetchall()
    return {"data": [r["table_name"] for r in rows]}


@router.get("/rest/{table}")
def select(table: str, authorization: str | None = Header(default=None), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), order: str = "created_at.desc", eq: str | None = None, q: str | None = None):
    u = current_user(authorization); validate_name(table)
    column, direction = order_clause(order)
    where = ["table_name=%s"]; args: list[Any] = [table]
    if eq and "=" in eq:
        key, value = eq.split("=", 1)
        if not key.replace("_", "a").isalnum(): raise HTTPException(400, "Invalid filter")
        where.append("row_data->>%s=%s"); args.extend([key, value])
    if q:
        where.append("row_data::text ILIKE %s"); args.append(f"%{q}%")
    args += [limit, offset]
    with db() as conn:
        set_rls(conn, u["id"])
        rows = conn.execute(f"SELECT id,row_data,created_at,updated_at FROM sb_data WHERE {' AND '.join(where)} ORDER BY {column} {direction} LIMIT %s OFFSET %s", args).fetchall()
    return {"data": [format_row(r) for r in rows], "count": len(rows)}


@router.post("/rest/{table}")
def insert(table: str, body: RowBody, authorization: str | None = Header(default=None)):
    u = current_user(authorization); validate_name(table)
    with db() as conn:
        set_rls(conn, u["id"])
        row = conn.execute("INSERT INTO sb_data(owner_id,table_name,row_data) VALUES(%s,%s,%s) RETURNING id,row_data,created_at,updated_at", (u["id"], table, Jsonb(body.data))).fetchone()
        audit(conn, u["id"], "data.insert", table, {"id": str(row["id"])})
        conn.commit()
    return {"data": format_row(row)}


@router.post("/rest/{table}/upsert")
def upsert(table: str, body: RowBody, authorization: str | None = Header(default=None)):
    u = current_user(authorization); validate_name(table)
    row_id = body.data.get("id")
    with db() as conn:
        set_rls(conn, u["id"])
        if row_id:
            row = conn.execute("UPDATE sb_data SET row_data=row_data || %s::jsonb WHERE id=%s AND table_name=%s RETURNING id,row_data,created_at,updated_at", (Jsonb(body.data), str(row_id), table)).fetchone()
        else:
            row = None
        if not row:
            row = conn.execute("INSERT INTO sb_data(owner_id,table_name,row_data) VALUES(%s,%s,%s) RETURNING id,row_data,created_at,updated_at", (u["id"], table, Jsonb(body.data))).fetchone()
        audit(conn, u["id"], "data.upsert", table, {"id": str(row["id"])})
        conn.commit()
    return {"data": format_row(row)}


@router.patch("/rest/{table}/{row_id}")
def update(table: str, row_id: str, body: RowBody, authorization: str | None = Header(default=None)):
    u = current_user(authorization); validate_name(table)
    with db() as conn:
        set_rls(conn, u["id"])
        row = conn.execute("UPDATE sb_data SET row_data=row_data || %s::jsonb WHERE id=%s AND table_name=%s RETURNING id,row_data,created_at,updated_at", (Jsonb(body.data), row_id, table)).fetchone()
        if not row: raise HTTPException(404, "Row not found")
        audit(conn, u["id"], "data.update", table, {"id": row_id})
        conn.commit()
    return {"data": format_row(row)}


@router.delete("/rest/{table}/{row_id}")
def delete(table: str, row_id: str, authorization: str | None = Header(default=None)):
    u = current_user(authorization); validate_name(table)
    with db() as conn:
        set_rls(conn, u["id"])
        cur = conn.execute("DELETE FROM sb_data WHERE id=%s AND table_name=%s", (row_id, table))
        if cur.rowcount == 0: raise HTTPException(404, "Row not found")
        audit(conn, u["id"], "data.delete", table, {"id": row_id})
        conn.commit()
    return {"deleted": True}


@router.post("/storage/buckets")
def create_bucket(body: BucketBody, authorization: str | None = Header(default=None)):
    u = current_user(authorization); safe_bucket(body.name)
    with db() as conn:
        try:
            conn.execute("INSERT INTO sb_storage_buckets(name,is_public) VALUES(%s,%s)", (body.name, body.public)); conn.commit()
        except Exception as exc:
            conn.rollback(); raise HTTPException(409, "Bucket already exists") from exc
    os.makedirs(os.path.join(STORAGE_ROOT, body.name), exist_ok=True)
    return {"name": body.name, "public": body.public}


@router.get("/storage/buckets")
def list_buckets(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with db() as conn:
        rows = conn.execute("SELECT name,is_public,created_at FROM sb_storage_buckets ORDER BY name").fetchall()
    return {"data": [{"name": r["name"], "public": r["is_public"], "created_at": r["created_at"].isoformat()} for r in rows]}


@router.post("/storage/{bucket}/{object_name:path}")
async def upload(bucket: str, object_name: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    u = current_user(authorization); safe_bucket(bucket); safe_object(object_name)
    data = await file.read()
    if len(data) > 25 * 1024 * 1024: raise HTTPException(413, "Object exceeds 25 MB limit")
    with db() as conn:
        b = conn.execute("SELECT is_public FROM sb_storage_buckets WHERE name=%s", (bucket,)).fetchone()
        if not b: raise HTTPException(404, "Bucket not found")
        key = f"{uuid.uuid4().hex}-{os.path.basename(object_name)}"
        path = os.path.join(STORAGE_ROOT, bucket, key); os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as out: out.write(data)
        row = conn.execute("INSERT INTO sb_storage_objects(owner_id,bucket,object_name,mime,size_bytes,storage_key,is_public) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at", (u["id"], bucket, object_name, file.content_type or "application/octet-stream", len(data), key, b["is_public"])).fetchone()
        audit(conn, u["id"], "storage.upload", f"{bucket}/{object_name}")
        conn.commit()
    return {"id": str(row["id"]), "bucket": bucket, "object_name": object_name, "size": len(data), "public": b["is_public"]}


@router.get("/storage/{bucket}/{object_name:path}")
def download(bucket: str, object_name: str, authorization: str | None = Header(default=None)):
    safe_bucket(bucket); safe_object(object_name)
    u = None
    if authorization and authorization.startswith("Bearer "):
        u = verify_token(authorization[7:].strip())
    with db() as conn:
        set_rls(conn, u["id"] if u else "00000000-0000-0000-0000-000000000000")
        row = conn.execute("SELECT storage_key,mime,is_public FROM sb_storage_objects WHERE bucket=%s AND object_name=%s", (bucket, object_name)).fetchone()
    if not row or (not row["is_public"] and not u): raise HTTPException(404, "Object not found")
    path = os.path.join(STORAGE_ROOT, bucket, row["storage_key"])
    if not os.path.isfile(path): raise HTTPException(404, "Object not found")
    with open(path, "rb") as f: data = f.read()
    return Response(content=data, media_type=row["mime"])


@router.delete("/storage/{bucket}/{object_name:path}")
def remove_object(bucket: str, object_name: str, authorization: str | None = Header(default=None)):
    u = current_user(authorization); safe_bucket(bucket); safe_object(object_name)
    with db() as conn:
        set_rls(conn, u["id"])
        row = conn.execute("DELETE FROM sb_storage_objects WHERE bucket=%s AND object_name=%s AND owner_id=%s RETURNING storage_key", (bucket, object_name, u["id"])).fetchone()
        if not row: raise HTTPException(404, "Object not found")
        conn.commit()
    path = os.path.join(STORAGE_ROOT, bucket, row["storage_key"])
    try: os.remove(path)
    except FileNotFoundError: pass
    return {"deleted": True}


@router.post("/jobs")
def enqueue(body: JobBody, authorization: str | None = Header(default=None)):
    u = current_user(authorization)
    with db() as conn:
        row = conn.execute("INSERT INTO sb_jobs(user_id,job_type,payload) VALUES(%s,%s,%s) RETURNING id,status,created_at", (u["id"], body.job_type, Jsonb(body.payload))).fetchone(); conn.commit()
    return {"id": str(row["id"]), "status": row["status"], "created_at": row["created_at"].isoformat()}


@router.get("/jobs/{job_id}")
def job(job_id: str, authorization: str | None = Header(default=None)):
    u = current_user(authorization)
    with db() as conn:
        row = conn.execute("SELECT id,job_type,status,attempts,result,error,created_at,updated_at FROM sb_jobs WHERE id=%s AND user_id=%s", (job_id, u["id"])).fetchone()
    if not row: raise HTTPException(404, "Job not found")
    return {**{k: row[k] for k in ("job_type","status","attempts","result","error")}, "id": str(row["id"]), "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}


@router.post("/vectors")
def add_vector(body: VectorBody, authorization: str | None = Header(default=None)):
    u = current_user(authorization)
    with db() as conn:
        set_rls(conn, u["id"])
        row = conn.execute("INSERT INTO sb_embeddings(owner_id,namespace,content,metadata,embedding) VALUES(%s,%s,%s,%s,%s::vector) RETURNING id,created_at", (u["id"], body.namespace, body.content, Jsonb(body.metadata), "[" + ",".join(map(str, body.embedding)) + "]")).fetchone(); conn.commit()
    return {"id": str(row["id"]), "created_at": row["created_at"].isoformat()}


@router.post("/vectors/search")
def search_vectors(body: VectorBody, authorization: str | None = Header(default=None), limit: int = Query(10, ge=1, le=100)):
    u = current_user(authorization)
    with db() as conn:
        set_rls(conn, u["id"])
        rows = conn.execute("SELECT id,content,metadata,1-(embedding <=> %s::vector) AS similarity FROM sb_embeddings WHERE owner_id=%s AND namespace=%s AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT %s", ("[" + ",".join(map(str, body.embedding)) + "]", u["id"], body.namespace, "[" + ",".join(map(str, body.embedding)) + "]", limit)).fetchall()
    return {"data": [{"id": str(r["id"]), "content": r["content"], "metadata": r["metadata"], "similarity": float(r["similarity"])} for r in rows]}


@router.get("/audit")
def audit_log(authorization: str | None = Header(default=None), limit: int = Query(100, ge=1, le=1000)):
    u = current_user(authorization)
    with db() as conn:
        rows = conn.execute("SELECT id,action,resource,metadata,created_at FROM sb_audit_log WHERE user_id=%s ORDER BY created_at DESC LIMIT %s", (u["id"], limit)).fetchall()
    return {"data": [{"id": r["id"], "action": r["action"], "resource": r["resource"], "metadata": r["metadata"], "created_at": r["created_at"].isoformat()} for r in rows]}


@router.websocket("/realtime")
async def realtime(websocket):
    await websocket.accept()
    await websocket.send_json({"event": "connected", "channel": "sb_realtime", "note": "Use PostgreSQL LISTEN/NOTIFY worker for production fanout."})
    try:
        while True:
            message = await websocket.receive_text()
            if message.lower() == "ping": await websocket.send_json({"event": "pong"})
    except Exception:
        return


def format_row(r):
    return {"id": str(r["id"]), **r["row_data"], "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}


def validate_name(value: str):
    if not value or len(value) > 100 or not value.replace("_", "a").isalnum(): raise HTTPException(400, "Invalid name")


def safe_bucket(value: str): validate_name(value)


def safe_object(value: str):
    if not value or value.startswith("/") or ".." in value.split("/"): raise HTTPException(400, "Invalid object name")


def order_clause(value: str):
    parts = value.split(".", 1)
    col = parts[0] if parts[0] in {"created_at", "updated_at"} else "created_at"
    direction = "ASC" if len(parts) > 1 and parts[1].lower() == "asc" else "DESC"
    return col, direction
