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
try:
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
except PermissionError:
    # GitHub-hosted CI cannot write to /data. Fall back to a workspace-local
    # directory while production Docker deployments continue using /data/storage.
    STORAGE_ROOT = Path(os.getenv("CI_STORAGE_ROOT", ".ci-storage"))
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
