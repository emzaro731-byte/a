import json
import os
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import main
from auth import login, register, track, verify_token
from db import usage_summary

app: FastAPI = main.app
DAILY_REQUEST_LIMIT = max(0, int(os.getenv("DAILY_REQUEST_LIMIT", "1000")))


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)


@app.middleware("http")
async def user_auth_bridge(request: Request, call_next):
    path = request.url.path
    if path.startswith("/auth/") or path in {"/health", "/ready", "/docs", "/openapi.json", "/redoc"}:
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        user = verify_token(token)
        if user:
            # The v6 API-key guard remains enabled; a valid user token is translated
            # internally to the server-side API key, never exposed to the client.
            headers = [(k, v) for k, v in request.scope.get("headers", []) if k.lower() not in {b"authorization", b"x-user-id"}]
            headers += [(b"authorization", f"Bearer {main.API_KEY}".encode()), (b"x-user-id", user["id"].encode())]
            request.scope["headers"] = headers
            request.state.user = user
            if path.startswith("/v1/") and path not in {"/v1/models", "/v1/config", "/v1/tools"}:
                today = time.time() - 86400
                used = sum(usage_summary(user["id"], today).values())
                if DAILY_REQUEST_LIMIT and used >= DAILY_REQUEST_LIMIT:
                    return main.JSONResponse(status_code=429, content={"error": {"message": "Daily usage limit reached", "type": "quota_error"}})
            response = await call_next(request)
            if path.startswith("/v1/"):
                track(user["id"], "api_request")
            return response
    return await call_next(request)


@app.post("/auth/register", tags=["auth"])
async def auth_register(body: AuthRequest):
    try:
        user = register(body.email, body.password, body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"user": user, "access_token": __import__("auth").issue_token({**user, "password_hash": ""}), "token_type": "bearer"}


@app.post("/auth/login", tags=["auth"])
async def auth_login(body: AuthRequest):
    try:
        return login(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.get("/auth/me", tags=["auth"])
async def auth_me(request: Request):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    user = verify_token(authorization[7:].strip())
    if not user:
        raise HTTPException(401, "Invalid or expired token")
    return {"id": user["id"], "email": user["email"], "name": user["name"]}


@app.get("/v1/usage", tags=["account"])
async def account_usage(request: Request):
    authorization = request.headers.get("Authorization", "")
    user = verify_token(authorization[7:].strip()) if authorization.startswith("Bearer ") else None
    if not user:
        raise HTTPException(401, "Valid user token required")
    since = time.time() - 86400
    usage = usage_summary(user["id"], since)
    return {"user_id": user["id"], "period": "24h", "daily_limit": DAILY_REQUEST_LIMIT or None, "used": sum(usage.values()), "by_kind": usage}
