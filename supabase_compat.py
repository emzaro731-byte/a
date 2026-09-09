"""Supabase-like REST, Storage and Realtime compatibility layer.

This is intentionally self-hosted: it does not depend on Supabase Cloud.
It provides familiar endpoint shapes for apps that want one backend for auth,
PostgREST-style data, storage and realtime. It is not a drop-in replacement
for every Supabase internal service.
"""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from auth import issue_token, verify_token
from db import DB_PATH, _conn

router = APIRouter(prefix="/supabase", tags=["supabase-compatible"])
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "/data/storage"))
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


def current_user(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    user = verify_token(authorization[7:].strip())
    if not user:
        raise HTTPException(401, "Invalid or expired token")
    return user


def init_compat_db() -> None:
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS sb_tables (
          id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, table_name TEXT NOT NULL,
          row_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sb_tables_name_owner ON sb_tables(table_name, owner_id);
        CREATE TABLE IF NOT EXISTS sb_storage (
          id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, bucket TEXT NOT NULL,
          object_name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
          path TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sb_storage_bucket ON sb_storage(owner_id,bucket);
        CREATE TABLE IF NOT EXISTS sb_functions (
          name TEXT PRIMARY KEY, code TEXT NOT NULL, updated_at REAL NOT NULL
        );
        ''')


init_compat_db()

class RowBody(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)

class FunctionBody(BaseModel):
    code: str = Field(min_length=1, max_length=200000)

@router.get("/auth/session")
async def session(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return {"access_token": issue_token(user), "token_type": "bearer", "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}

@router.get("/rest/tables")
async def tables(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    with _conn() as c:
        rows = c.execute("SELECT DISTINCT table_name FROM sb_tables WHERE owner_id=? ORDER BY table_name", (user["id"],)).fetchall()
    return {"data": [r[0] for r in rows]}

@router.get("/rest/{table}")
async def select_rows(table: str, authorization: str | None = Header(default=None), limit: int = 100, offset: int = 0):
    user = current_user(authorization)
    limit = min(max(limit, 1), 1000); offset = max(offset, 0)
    with _conn() as c:
        rows = c.execute("SELECT id,row_json,created_at,updated_at FROM sb_tables WHERE owner_id=? AND table_name=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (user["id"], table, limit, offset)).fetchall()
    return {"data": [{"id": r[0], **json.loads(r[1]), "created_at": r[2], "updated_at": r[3]} for r in rows]}

@router.post("/rest/{table}")
async def insert_row(table: str, body: RowBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization); rid = uuid.uuid4().hex; now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO sb_tables VALUES (?,?,?,?,?,?)", (rid, user["id"], table[:100], json.dumps(body.data), now, now))
    return {"data": {"id": rid, **body.data, "created_at": now, "updated_at": now}}

@router.patch("/rest/{table}/{row_id}")
async def update_row(table: str, row_id: str, body: RowBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    with _conn() as c:
        old = c.execute("SELECT row_json FROM sb_tables WHERE id=? AND owner_id=? AND table_name=?", (row_id, user["id"], table)).fetchone()
        if not old: raise HTTPException(404, "Row not found")
        merged = json.loads(old[0]); merged.update(body.data); now = time.time()
        c.execute("UPDATE sb_tables SET row_json=?,updated_at=? WHERE id=? AND owner_id=?", (json.dumps(merged), now, row_id, user["id"]))
    return {"data": {"id": row_id, **merged, "updated_at": now}}

@router.delete("/rest/{table}/{row_id}")
async def delete_row(table: str, row_id: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    with _conn() as c:
        cur = c.execute("DELETE FROM sb_tables WHERE id=? AND owner_id=? AND table_name=?", (row_id, user["id"], table))
    if cur.rowcount == 0: raise HTTPException(404, "Row not found")
    return {"data": None, "deleted": True}

@router.post("/storage/upload")
async def storage_upload(bucket: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if not bucket or len(bucket) > 100: raise HTTPException(400, "Invalid bucket")
    raw = await file.read()
    if len(raw) > 25_000_000: raise HTTPException(413, "Maximum object size is 25 MB")
    safe_name = Path(file.filename or "file").name
    oid = uuid.uuid4().hex; directory = STORAGE_ROOT / user["id"] / bucket; directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{oid}_{safe_name}"
    target.write_bytes(raw)
    with _conn() as c:
        c.execute("INSERT INTO sb_storage VALUES (?,?,?,?,?,?,?)", (oid,user["id"],bucket,safe_name,file.content_type or "application/octet-stream",len(raw),str(target),time.time()))
    return {"id": oid, "bucket": bucket, "name": safe_name, "size": len(raw)}

@router.get("/storage/objects")
async def storage_list(bucket: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    with _conn() as c:
        rows = c.execute("SELECT id,object_name,mime,size,created_at FROM sb_storage WHERE owner_id=? AND bucket=? ORDER BY created_at DESC", (user["id"],bucket)).fetchall()
    return {"data": [dict(r) for r in rows]}

@router.delete("/storage/objects/{object_id}")
async def storage_delete(object_id: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    with _conn() as c:
        row = c.execute("SELECT path FROM sb_storage WHERE id=? AND owner_id=?", (object_id,user["id"])).fetchone()
        if not row: raise HTTPException(404,"Object not found")
        c.execute("DELETE FROM sb_storage WHERE id=? AND owner_id=?", (object_id,user["id"]))
    try: Path(row[0]).unlink(missing_ok=True)
    except OSError: pass
    return {"deleted": True}

@router.post("/functions/{name}")
async def function_invoke(name: str, request: Request, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    with _conn() as c:
        row = c.execute("SELECT code FROM sb_functions WHERE name=?", (name,)).fetchone()
    if not row: raise HTTPException(404,"Function not found")
    # Functions are stored as metadata only. Arbitrary Python execution is deliberately disabled.
    return {"name": name, "status": "registered", "user_id": user["id"], "message": "Function execution requires a trusted worker; arbitrary code execution is disabled."}

@router.put("/functions/{name}")
async def function_register(name: str, body: FunctionBody, authorization: str | None = Header(default=None)):
    current_user(authorization)
    with _conn() as c:
        c.execute("INSERT INTO sb_functions(name,code,updated_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET code=excluded.code,updated_at=excluded.updated_at", (name[:100],body.code,time.time()))
    return {"name": name, "registered": True}

class RealtimeHub:
    clients: set[WebSocket] = set()

hub = RealtimeHub()

@router.websocket("/realtime")
async def realtime(ws: WebSocket):
    await ws.accept(); hub.clients.add(ws)
    try:
        await ws.send_json({"type":"system","event":"connected","timestamp":time.time()})
        while True:
            message = await ws.receive_text()
            if message == "ping": await ws.send_json({"type":"system","event":"pong"})
    except WebSocketDisconnect:
        hub.clients.discard(ws)
