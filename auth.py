import base64
import hashlib
import hmac
import os
import time
import uuid
from typing import Any

from db import create_user, get_user_by_email, get_user_by_id, record_usage

AUTH_SECRET = os.getenv("AUTH_SECRET", "")
TOKEN_TTL_SECONDS = max(300, int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "604800")))


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
    payload = _b64((__import__("json").dumps({
        "sub": user["id"], "email": user["email"], "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
        "jti": uuid.uuid4().hex,
    }, separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    signature = _b64(hmac.new(_secret(), signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def verify_token(token: str) -> dict[str, Any] | None:
    try:
        header, payload, signature = token.split(".", 2)
        expected = _b64(hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = __import__("json").loads(_unb64(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        user = get_user_by_id(data.get("sub", ""))
        return user if user else None
    except Exception:
        return None


def register(email: str, password: str, name: str = "") -> dict[str, Any]:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("Enter a valid email address")
    if get_user_by_email(email):
        raise ValueError("An account with this email already exists")
    user = create_user(email, hash_password(password), name.strip()[:120])
    return {"id": user["id"], "email": user["email"], "name": user["name"]}


def login(email: str, password: str) -> dict[str, Any]:
    user = get_user_by_email(email.strip().lower())
    if not user or not verify_password(password, user["password_hash"]):
        raise ValueError("Invalid email or password")
    return {"user": {"id": user["id"], "email": user["email"], "name": user["name"]}, "access_token": issue_token(user), "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS}


def track(user_id: str, kind: str, amount: int = 1) -> None:
    try:
        record_usage(user_id, kind, amount)
    except Exception:
        pass
