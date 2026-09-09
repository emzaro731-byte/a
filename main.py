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

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("AI_MODEL", "qwen2.5:3b")
FAST_MODEL = os.getenv("AI_FAST_MODEL", DEFAULT_MODEL)
REASONING_MODEL = os.getenv("AI_REASONING_MODEL", DEFAULT_MODEL)
CODING_MODEL = os.getenv("AI_CODING_MODEL", DEFAULT_MODEL)
API_KEY = os.getenv("AI_API_KEY", "")
SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    "You are a helpful, accurate, concise AI assistant. Think carefully, explain clearly, and never invent facts when you are uncertain.",
)
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

app = FastAPI(title="My AI API", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(media_router)

_hits: dict[str, deque[float]] = defaultdict(deque)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message] = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    stream: bool = False


def check_api_key(authorization: str | None = Header(default=None)) -> None:
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid API key")


def rate_limit(request: Request) -> None:
    if RATE_LIMIT <= 0:
        return
    key = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _hits[key]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    bucket.append(now)


def resolve_model(name: str | None) -> str:
    aliases = {None: DEFAULT_MODEL, "fast": FAST_MODEL, "reasoning": REASONING_MODEL, "coding": CODING_MODEL, "default": DEFAULT_MODEL}
    model = aliases.get(name, name)
    if not model:
        raise HTTPException(status_code=400, detail="No AI model configured")
    return model


def build_payload(request: ChatRequest, model: str, stream: bool) -> dict[str, Any]:
    messages = [m.model_dump() for m in request.messages]
    if not any(m["role"] == "system" for m in messages) and SYSTEM_PROMPT:
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream, "options": {"temperature": request.temperature}}
    if request.max_tokens:
        payload["options"]["num_predict"] = request.max_tokens
    return payload


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        return {"status": "ok", "ollama": "connected", "model": DEFAULT_MODEL, "version": "3.0.0"}
    except Exception as exc:
        return {"status": "degraded", "ollama": "unavailable", "error": str(exc), "version": "3.0.0"}


@app.get("/v1/models", dependencies=[Depends(check_api_key)])
async def models() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        data = response.json()
    return {"object": "list", "data": [{"id": item["name"], "object": "model", "owned_by": "local"} for item in data.get("models", [])]}


@app.get("/v1/config", dependencies=[Depends(check_api_key)])
async def config() -> dict[str, Any]:
    return {"default": DEFAULT_MODEL, "fast": FAST_MODEL, "reasoning": REASONING_MODEL, "coding": CODING_MODEL}


async def stream_from_ollama(request: ChatRequest, model: str, completion_id: str):
    payload = build_payload(request, model, True)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    text = data.get("message", {}).get("content", "")
                    if text:
                        chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
                        yield f"data: {json.dumps(chunk)}\n\n"
                    if data.get("done"):
                        final = {"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                        yield f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n"
    except httpx.HTTPStatusError as exc:
        yield f"data: {json.dumps({'error': {'message': f'AI model error: {exc.response.text[:1000]}'}})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'error': {'message': f'AI model unavailable: {exc}'}})}\n\n"


@app.post("/v1/chat/completions", dependencies=[Depends(check_api_key), Depends(rate_limit)])
async def chat(request: ChatRequest):
    model = resolve_model(request.model)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    if request.stream:
        return StreamingResponse(stream_from_ollama(request, model, completion_id), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    payload = build_payload(request, model, False)
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"AI model error: {exc.response.text[:1000]}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"AI model unavailable: {exc}") from exc
    answer = data.get("message", {}).get("content", "")
    prompt_tokens = data.get("prompt_eval_count", 0) or 0
    completion_tokens = data.get("eval_count", 0) or 0
    return {"id": completion_id, "object": "chat.completion", "created": int(time.time()), "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}, "x_latency_seconds": round(time.time() - started, 3)}


@app.post("/chat", dependencies=[Depends(check_api_key), Depends(rate_limit)])
async def simple_chat(request: ChatRequest):
    return await chat(request)
