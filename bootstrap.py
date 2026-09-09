import os
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import main
from auth import TOKEN_TTL_SECONDS, issue_token, login, register, track, verify_token
from db import usage_summary
from gateway import router as gateway_router
from supabase_v9 import router as supabase_v9_router

app: FastAPI = main.app
app.version = "10.1.0"
app.include_router(supabase_v9_router)
app.include_router(gateway_router)
DAILY_REQUEST_LIMIT = max(0, int(os.getenv("DAILY_REQUEST_LIMIT", "1000")))


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)


@app.middleware("http")
async def user_auth_bridge(request: Request, call_next):
    path = request.url.path
    # Supabase-compatible and gateway routes must retain their Bearer token so
    # their own authentication can inspect it. Do not rewrite these headers to
    # the legacy API key format.
    if path.startswith("/auth/") or path.startswith("/supabase/") or path.startswith("/v1/gateway/") or path in {"/health", "/ready", "/docs", "/openapi.json", "/redoc"}:
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        user = verify_token(token)
        if user:
            headers = [(k, v) for k, v in request.scope.get("headers", []) if k.lower() not in {b"authorization", b"x-user-id"}]
            headers += [(b"authorization", f"Bearer {main.API_KEY}".encode()), (b"x-user-id", user["id"].encode())]
            request.scope["headers"] = headers
            request.state.user = user
            if path.startswith("/v1/") and path not in {"/v1/models", "/v1/config", "/v1/tools"}:
                used = sum(usage_summary(user["id"], time.time() - 86400).values())
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
    return {"user": user, "access_token": issue_token(user), "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS}


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
    return user


@app.get("/v1/usage", tags=["account"])
async def account_usage(request: Request):
    authorization = request.headers.get("Authorization", "")
    user = verify_token(authorization[7:].strip()) if authorization.startswith("Bearer ") else None
    if not user:
        raise HTTPException(401, "Valid user token required")
    usage = usage_summary(user["id"], time.time() - 86400)
    return {"user_id": user["id"], "period": "24h", "daily_limit": DAILY_REQUEST_LIMIT or None, "used": sum(usage.values()), "by_kind": usage}
