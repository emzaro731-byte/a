import json
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from media import router as media_router

STARTED_AT = time.time()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("AI_MODEL", "qwen2.5:3b")
FAST_MODEL = os.getenv("AI_FAST_MODEL", DEFAULT_MODEL)
REASONING_MODEL = os.getenv("AI_REASONING_MODEL", DEFAULT_MODEL)
CODING_MODEL = os.getenv("AI_CODING_MODEL", DEFAULT_MODEL)
API_KEY = os.getenv("AI_API_KEY", "")
REQUIRE_API_KEY = os.getenv("REQUIRE_API_KEY", "true").lower() == "true"
RATE_LIMIT = max(0, int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")))
SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    "You are a helpful, accurate, concise AI assistant. Think carefully, explain clearly, and never invent facts when you are uncertain.",
)

# Direct model names are restricted to this allow-list. Aliases are always supported.
_configured_models = [DEFAULT_MODEL, FAST_MODEL, REASONING_MODEL, CODING_MODEL]
ALLOWED_MODELS = {
    item.strip() for item in os.getenv("ALLOWED_MODELS", ",".join(_configured_models)).split(",") if item.strip()
}

app = FastAPI(
    title="My AI API",
    version="4.0.0",
    description="Self-hosted, OpenAI-compatible AI gateway for chat and media generation.",
)

origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
app.include_router(media_router)

_hits: dict[str, deque[float]] = defaultdict(deque)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100000)


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message] = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    stream: bool = False


def check_api_key(authorization: str | None = Header(default=None)) -> None:
    if REQUIRE_API_KEY and not API_KEY:
        raise HTTPException(status_code=503, detail="API is not configured with an API key")
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid API key")


def rate_limit(request: Request) -> None:
    if RATE_LIMIT <= 0:
        return
    forwarded = request.headers.get("x-forwarded-for", "")
    key = forwarded.split(",", 1)[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    now = time.time()
    bucket = _hits[key]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    bucket.append(now)


def resolve_model(name: str | None) -> str:
    aliases = {
        None: DEFAULT_MODEL,
        "default": DEFAULT_MODEL,
        "fast": FAST_MODEL,
        "reasoning": REASONING_MODEL,
        "coding": CODING_MODEL,
    }
    model = aliases.get(name, name)
    if not model:
        raise HTTPException(status_code=400, detail="No AI model configured")
    if model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail="Requested model is not allowed")
    return model


def build_payload(request: ChatRequest, model: str, stream: bool) -> dict[str, Any]:
    messages = [m.model_dump() for m in request.messages]
    if not any(m["role"] == "system" for m in messages) and SYSTEM_PROMPT:
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": {"temperature": request.temperature},
    }
    if request.max_tokens:
        payload["options"]["num_predict"] = request.max_tokens
    return payload


@app.middleware("http")
async def request_security(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    # Protect every API endpoint except public operational checks and CORS preflight.
    protected = request.url.path.startswith("/v1/") or request.url.path == "/chat"
    if protected and request.method != "OPTIONS":
        authorization = request.headers.get("Authorization")
        if REQUIRE_API_KEY and not API_KEY:
            response = await _json_error(503, "API is not configured with an API key")
            response.headers["X-Request-ID"] = request_id
            return response
        if API_KEY and authorization != f"Bearer {API_KEY}":
            response = await _json_error(401, "Invalid API key")
            response.headers["X-Request-ID"] = request_id
            return response
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def _json_error(status_code: int, message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"error": {"message": message, "type": "api_error"}})


async def ollama_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            return True
    except Exception:
        return False


@app.get("/health")
async def health() -> dict[str, Any]:
    connected = await ollama_reachable()
    return {
        "status": "ok" if connected else "degraded",
        "service": "my-ai-api",
        "version": app.version,
        "ollama": "connected" if connected else "unavailable",
        "model": DEFAULT_MODEL,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
    }


@app.get("/ready")
async def ready() -> dict[str, Any]:
    if not await ollama_reachable():
        raise HTTPException(status_code=503, detail="AI backend is not ready")
    return {"ready": True, "model": DEFAULT_MODEL, "version": app.version}


@app.get("/v1/models", dependencies=[Depends(check_api_key)])
async def models() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="AI backend returned an error") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="AI backend is unavailable") from exc
    return {
        "object": "list",
        "data": [
            {"id": item["name"], "object": "model", "owned_by": "local"}
            for item in data.get("models", [])
            if item.get("name") in ALLOWED_MODELS
        ],
    }


@app.get("/v1/config", dependencies=[Depends(check_api_key)])
async def config() -> dict[str, Any]:
    return {
        "default": DEFAULT_MODEL,
        "fast": FAST_MODEL,
        "reasoning": REASONING_MODEL,
        "coding": CODING_MODEL,
        "allowed_models": sorted(ALLOWED_MODELS),
        "version": app.version,
    }


async def stream_from_ollama(request: ChatRequest, model: str, completion_id: str):
    payload = build_payload(request, model, True)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = data.get("message", {}).get("content", "")
                    if text:
                        chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                    if data.get("done"):
                        final = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                        yield f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n"
    except httpx.HTTPStatusError:
        yield f"data: {json.dumps({'error': {'message': 'AI model returned an error', 'type': 'model_error'}})}\n\n"
    except Exception:
        yield f"data: {json.dumps({'error': {'message': 'AI model became unavailable', 'type': 'model_error'}})}\n\n"


@app.post("/v1/chat/completions", dependencies=[Depends(check_api_key), Depends(rate_limit)])
async def chat(request: ChatRequest):
    model = resolve_model(request.model)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    if request.stream:
        return StreamingResponse(
            stream_from_ollama(request, model, completion_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    payload = build_payload(request, model, False)
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="AI model returned an error") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="AI model is unavailable") from exc

    answer = data.get("message", {}).get("content", "")
    prompt_tokens = data.get("prompt_eval_count", 0) or 0
    completion_tokens = data.get("eval_count", 0) or 0
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "x_latency_seconds": round(time.time() - started, 3),
    }


@app.post("/chat", dependencies=[Depends(check_api_key), Depends(rate_limit)])
async def simple_chat(request: ChatRequest):
    return await chat(request)
