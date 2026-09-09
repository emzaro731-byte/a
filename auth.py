import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Any

from db import create_user, get_user_by_email, get_user_by_id, record_usage

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

AUTH_SECRET = os.getenv("AUTH_SECRET", "")
TOKEN_TTL_SECONDS = max(300, int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "604800")))
PG_DSN = os.getenv("POSTGRES_DSN", "")
REFRESH_TOKEN_TTL_SECONDS = max(3600, int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", str(60 * 60 * 24 * 30))))


def _secret() -> bytes:
    if len(AUTH_SECRET) < 32:
        raise RuntimeError("AUTH_SECRET must be set to a random value of at least 32 characters")
    return AUTH_SECRET.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    if not 8 <= len(password) <= 200:
        raise ValueError("Password must be 8-200 characters")
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(rounds))
        return hmac.compare_digest(_b64(actual), expected)
    except Exception:
        return False


def issue_token(user: dict[str, Any]) -> str:
    header = _b64(b'{"alg":"HS256","typ":"JWT"}')
    now = int(time.time())
    payload_data = {
        "sub": str(user["id"]),
        "email": user["email"],
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "jti": uuid.uuid4().hex,
    }
    payload = _b64(json.dumps(payload_data, separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    signature = _b64(hmac.new(_secret(), signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _pg():
    if not (PG_DSN and psycopg):
        return None
    return psycopg.connect(PG_DSN, row_factory=dict_row)


def _public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "name": user.get("name", ""),
        "email_verified": bool(user.get("email_verified", False)),
    }


def verify_token(token: str) -> dict[str, Any] | None:
    try:
        header, payload, signature = token.split(".", 2)
        expected = _b64(hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        uid = str(data.get("sub", ""))
        conn = _pg()
        if conn:
            with conn:
                row = conn.execute(
                    "SELECT id,email,name,email_verified FROM sb_users WHERE id=%s", (uid,)
                ).fetchone()
            return _public(row) if row else None
        user = get_user_by_id(uid)
        return user if user else None
    except Exception:
        return None


def _pg_register(email: str, password: str, name: str) -> dict[str, Any]:
    conn = _pg()
    if not conn:
        raise RuntimeError("PostgreSQL authentication is not configured")
    try:
        with conn:
            row = conn.execute(
                "INSERT INTO sb_users(email,name,password_hash) VALUES(%s,%s,%s) RETURNING id,email,name,email_verified",
                (email, name, hash_password(password)),
            ).fetchone()
            conn.commit()
    except Exception as exc:
        if "duplicate key" in str(exc).lower() or "unique" in str(exc).lower():
            raise ValueError("An account with this email already exists") from exc
        raise
    return _public(row)


def _pg_login(email: str, password: str) -> dict[str, Any]:
    conn = _pg()
    if not conn:
        raise RuntimeError("PostgreSQL authentication is not configured")
    with conn:
        row = conn.execute(
            "SELECT id,email,name,password_hash,email_verified FROM sb_users WHERE email=%s", (email,)
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise ValueError("Invalid email or password")
        refresh = secrets.token_urlsafe(48)
        conn.execute(
            "INSERT INTO sb_sessions(user_id,refresh_token_hash,expires_at) VALUES(%s,%s,now()+make_interval(secs => %s))",
            (row["id"], hashlib.sha256(refresh.encode()).hexdigest(), REFRESH_TOKEN_TTL_SECONDS),
        )
        conn.commit()
    return {
        "user": _public(row),
        "access_token": issue_token(row),
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "refresh_expires_in": REFRESH_TOKEN_TTL_SECONDS,
    }


def register(email: str, password: str, name: str = "") -> dict[str, Any]:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("Enter a valid email address")
    if PG_DSN and psycopg:
        return _pg_register(email, password, name.strip()[:120])
    if get_user_by_email(email):
        raise ValueError("An account with this email already exists")
    user = create_user(email, hash_password(password), name.strip()[:120])
    return {"id": user["id"], "email": user["email"], "name": user["name"]}


def login(email: str, password: str) -> dict[str, Any]:
    email = email.strip().lower()
    if PG_DSN and psycopg:
        return _pg_login(email, password)
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        raise ValueError("Invalid email or password")
    return {
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        "access_token": issue_token(user),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
    }


def track(user_id: str, kind: str, amount: int = 1) -> None:
    try:
        record_usage(user_id, kind, amount)
    except Exception:
        pass
