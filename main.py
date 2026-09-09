import os
import time
import uuid
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.getenv("AI_MODEL", "qwen2.5:3b")
API_KEY = os.getenv("AI_API_KEY", "")

app = FastAPI(title="My AI API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    stream: bool = False


def check_api_key(authorization: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        return {"status": "ok", "ollama": "connected", "model": DEFAULT_MODEL}
    except Exception as exc:
        return {"status": "degraded", "ollama": "unavailable", "error": str(exc)}


@app.get("/v1/models", dependencies=[Depends(check_api_key)])
async def models() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        data = response.json()
    return {
        "object": "list",
        "data": [
            {"id": item["name"], "object": "model", "owned_by": "local"}
            for item in data.get("models", [])
        ],
    }


@app.post("/v1/chat/completions", dependencies=[Depends(check_api_key)])
async def chat(request: ChatRequest) -> dict[str, Any]:
    if request.stream:
        raise HTTPException(status_code=400, detail="Streaming is not enabled in this starter API yet")

    model = request.model or DEFAULT_MODEL
    payload: dict[str, Any] = {
        "model": model,
        "messages": [message.model_dump() for message in request.messages],
        "stream": False,
        "options": {"temperature": request.temperature},
    }
    if request.max_tokens:
        payload["options"]["num_predict"] = request.max_tokens

    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000]
        raise HTTPException(status_code=502, detail=f"AI model error: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"AI model unavailable: {exc}") from exc

    answer = data.get("message", {}).get("content", "")
    created = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
        },
        "x_latency_seconds": round(time.time() - started, 3),
    }


@app.post("/chat", dependencies=[Depends(check_api_key)])
async def simple_chat(request: ChatRequest) -> dict[str, Any]:
    return await chat(request)
