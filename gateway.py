import json
import os
import time
import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/gateway", tags=["model-gateway"])

# Each provider is configured through environment variables.  The gateway is
# OpenAI-compatible at its public boundary, while local Ollama remains the
# zero-cost default.  A provider can be any service exposing /v1/chat/completions.
PROVIDERS = {
    "local": {
        "base_url": os.getenv("LOCAL_AI_BASE_URL", os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/"),
        "api_key": os.getenv("LOCAL_AI_API_KEY", ""),
        "kind": "ollama",
    },
    "openai": {
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "kind": "openai",
    },
    "provider2": {
        "base_url": os.getenv("AI_PROVIDER2_BASE_URL", "").rstrip("/"),
        "api_key": os.getenv("AI_PROVIDER2_API_KEY", ""),
        "kind": "openai",
    },
    "provider3": {
        "base_url": os.getenv("AI_PROVIDER3_BASE_URL", "").rstrip("/"),
        "api_key": os.getenv("AI_PROVIDER3_API_KEY", ""),
        "kind": "openai",
    },
}

# Examples:
# fast -> local fast model; reasoning -> local reasoning model;
# coding/vision can be mapped to any provider/model without changing the app.
ROUTES = {
    "default": os.getenv("AI_GATEWAY_DEFAULT", "local:qwen2.5:3b"),
    "fast": os.getenv("AI_GATEWAY_FAST", "local:qwen2.5:3b"),
    "reasoning": os.getenv("AI_GATEWAY_REASONING", "local:qwen2.5:3b"),
    "coding": os.getenv("AI_GATEWAY_CODING", "local:qwen2.5:3b"),
    "vision": os.getenv("AI_GATEWAY_VISION", "local:qwen2.5:3b"),
}

class GatewayMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Any = Field(min_length=1)

class GatewayRequest(BaseModel):
    model: str | None = None
    messages: list[GatewayMessage] = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    stream: bool = False
    fallback: bool = True


def _target(name: str | None) -> tuple[str, str]:
    raw = name or "default"
    raw = ROUTES.get(raw, raw)
    if ":" not in raw:
        raw = ROUTES.get("default", raw)
    provider, model = raw.split(":", 1)
    if provider not in PROVIDERS or not model:
        raise HTTPException(400, "Unknown gateway model route")
    if provider != "local" and not PROVIDERS[provider]["base_url"]:
        raise HTTPException(503, f"Provider '{provider}' is not configured")
    return provider, model


def _headers(provider: str) -> dict[str, str]:
    key = PROVIDERS[provider]["api_key"]
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"} if key else {"Content-Type": "application/json"}


def _payload(provider: str, model: str, body: GatewayRequest) -> dict[str, Any]:
    messages = [m.model_dump() for m in body.messages]
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": body.temperature, "stream": False}
    if body.max_tokens:
        payload["max_tokens"] = body.max_tokens
    return payload


async def _call(provider: str, model: str, body: GatewayRequest) -> dict[str, Any]:
    cfg = PROVIDERS[provider]
    async with httpx.AsyncClient(timeout=300) as client:
        if cfg["kind"] == "ollama":
            payload = {"model": model, "messages": [m.model_dump() for m in body.messages], "stream": False, "options": {"temperature": body.temperature}}
            if body.max_tokens:
                payload["options"]["num_predict"] = body.max_tokens
            response = await client.post(f"{cfg['base_url']}/api/chat", headers=_headers(provider), json=payload)
            response.raise_for_status()
            data = response.json()
            answer = data.get("message", {}).get("content", "")
            prompt = data.get("prompt_eval_count", 0) or 0
            completion = data.get("eval_count", 0) or 0
        else:
            response = await client.post(f"{cfg['base_url']}/chat/completions", headers=_headers(provider), json=_payload(provider, model, body))
            response.raise_for_status()
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            answer = (choice.get("message") or {}).get("content", "")
            usage = data.get("usage") or {}
            prompt = usage.get("prompt_tokens", 0) or 0
            completion = usage.get("completion_tokens", 0) or 0
    return {
        "id": f"gw-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "model": f"{provider}:{model}",
        "provider": provider,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
        "latency_ms": 0,
    }


@router.get("/models")
async def gateway_models():
    data = []
    for capability, route in ROUTES.items():
        provider, model = _target(route)
        data.append({"id": capability, "route": f"{provider}:{model}", "provider": provider, "model": model})
    return {"object": "list", "data": data}


@router.get("/providers")
async def gateway_providers():
    return {"data": [{"id": name, "configured": bool(cfg["base_url"]) and (name == "local" or bool(cfg["api_key"]))} for name, cfg in PROVIDERS.items()]}


@router.post("/chat/completions")
async def gateway_chat(body: GatewayRequest, authorization: str | None = Header(default=None)):
    # The parent API middleware handles the main API key. This guard also
    # protects direct router use when the gateway is mounted elsewhere.
    api_key = os.getenv("AI_API_KEY", "")
    if api_key and authorization != f"Bearer {api_key}":
        raise HTTPException(401, "Invalid API key")

    provider, model = _target(body.model)
    started = time.perf_counter()
    try:
        result = await _call(provider, model, body)
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result
    except Exception as first_error:
        if not body.fallback:
            raise HTTPException(503, f"Provider '{provider}' failed") from first_error

        # Automatic fallback: try the default route if it differs from the
        # failed route. This keeps a client alive when an external provider is
        # temporarily unavailable.
        fallback_provider, fallback_model = _target("default")
        if (fallback_provider, fallback_model) == (provider, model):
            raise HTTPException(503, "AI provider is unavailable") from first_error
        try:
            result = await _call(fallback_provider, fallback_model, body)
            result["fallback_used"] = True
            result["fallback_from"] = f"{provider}:{model}"
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            return result
        except Exception as second_error:
            raise HTTPException(503, "All configured AI providers are unavailable") from second_error
