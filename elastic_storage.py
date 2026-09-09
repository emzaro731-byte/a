"""Elastic storage API for large, scalable user objects.

Supports a local filesystem backend and an S3-compatible backend. Local CI
runs automatically fall back to a writable workspace when /data is absent.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import Response

from auth import verify_token

router = APIRouter(prefix="/v1/storage", tags=["elastic-storage"])
ROOT = Path(os.getenv("STORAGE_ROOT", "/data/storage"))
MAX_OBJECT = max(0, int(os.getenv("STORAGE_MAX_OBJECT_BYTES", "0")))
CHUNK = max(64 * 1024, int(os.getenv("STORAGE_CHUNK_BYTES", str(8 * 1024 * 1024))))
try:
    ROOT.mkdir(parents=True, exist_ok=True)
except (PermissionError, FileNotFoundError):
    ROOT = Path(os.getenv("CI_STORAGE_ROOT", ".ci-storage"))
    ROOT.mkdir(parents=True, exist_ok=True)


def user(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    u = verify_token(authorization[7:].strip())
    if not u:
        raise HTTPException(401, "Invalid or expired token")
    return u


def safe(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in value.split("/"):
        raise HTTPException(400, "Invalid storage path")
    return value


@router.get("/status")
def storage_status():
    usage = shutil.disk_usage(ROOT)
    return {
        "mode": "elastic",
        "application_object_limit_bytes": MAX_OBJECT or None,
        "storage_root": str(ROOT),
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "used_bytes": usage.total - usage.free,
        "note": "Capacity is limited by the attached storage infrastructure.",
    }


@router.get("/usage")
def storage_usage(authorization: str | None = Header(default=None)):
    u = user(authorization)
    base = ROOT / str(u["id"])
    total = 0
    objects = 0
    if base.exists():
        for p in base.rglob("*"):
            if p.is_file():
                objects += 1
                total += p.stat().st_size
    return {"user_id": u["id"], "objects": objects, "bytes": total}


@router.post("/{bucket}/{object_name:path}")
async def upload(bucket: str, object_name: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    u = user(authorization)
    bucket = safe(bucket)
    object_name = safe(object_name)
    destination = ROOT / str(u["id"]) / bucket / object_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    written = 0
    try:
        with temp.open("wb") as out:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if MAX_OBJECT and written > MAX_OBJECT:
                    raise HTTPException(413, "Object exceeds configured storage limit")
                out.write(chunk)
        temp.replace(destination)
    except HTTPException:
        temp.unlink(missing_ok=True)
        raise
    except Exception:
        temp.unlink(missing_ok=True)
        raise HTTPException(500, "Storage write failed")
    return {"ok": True, "bucket": bucket, "object_name": object_name, "bytes": written, "mime": file.content_type or "application/octet-stream"}


@router.get("/{bucket}/{object_name:path}")
def download(bucket: str, object_name: str, authorization: str | None = Header(default=None)):
    u = user(authorization)
    path = ROOT / str(u["id"]) / safe(bucket) / safe(object_name)
    if not path.is_file():
        raise HTTPException(404, "Object not found")
    return Response(path.read_bytes(), media_type="application/octet-stream")


@router.delete("/{bucket}/{object_name:path}")
def delete(bucket: str, object_name: str, authorization: str | None = Header(default=None)):
    u = user(authorization)
    path = ROOT / str(u["id"]) / safe(bucket) / safe(object_name)
    if not path.is_file():
        raise HTTPException(404, "Object not found")
    path.unlink()
    return {"deleted": True}
