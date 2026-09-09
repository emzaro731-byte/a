import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1", tags=["media generation"])

IMAGE_URL = os.getenv("IMAGE_GENERATOR_URL", "").rstrip("/")
VIDEO_URL = os.getenv("VIDEO_GENERATOR_URL", "").rstrip("/")
MUSIC_URL = os.getenv("MUSIC_GENERATOR_URL", "").rstrip("/")
MEDIA_TOKEN = os.getenv("MEDIA_GENERATOR_TOKEN", "")


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    size: str = Field(default="1024x1024", pattern=r"^\d{3,4}x\d{3,4}$")
    n: int = Field(default=1, ge=1, le=4)
    seed: int | None = None


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    duration: int = Field(default=5, ge=1, le=30)
    width: int = Field(default=1024, ge=256, le=1920)
    height: int = Field(default=576, ge=256, le=1920)
    seed: int | None = None


class MusicRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    duration: int = Field(default=30, ge=5, le=300)
    instrumental: bool = True
    bpm: int | None = Field(default=None, ge=40, le=240)
    seed: int | None = None


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if MEDIA_TOKEN:
        headers["Authorization"] = f"Bearer {MEDIA_TOKEN}"
    return headers


async def _generate(url: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not url:
        raise HTTPException(status_code=503, detail=f"{kind} generation is not configured")
    try:
        async with httpx.AsyncClient(timeout=900) as client:
            response = await client.post(url, json=payload, headers=_headers())
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"{kind} generator returned an error") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"{kind} generator is unavailable") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"{kind} generator returned invalid JSON")
    return data


@router.get("/media/capabilities")
async def media_capabilities() -> dict[str, Any]:
    return {
        "object": "media.capabilities",
        "image": bool(IMAGE_URL),
        "video": bool(VIDEO_URL),
        "music": bool(MUSIC_URL),
        "local_only": True,
        "description": "Generation is routed to self-hosted/local model servers.",
    }


@router.post("/images/generations")
async def generate_image(request: ImageRequest) -> dict[str, Any]:
    payload = request.model_dump(exclude_none=True)
    payload["response_format"] = "url"
    result = await _generate(IMAGE_URL, "Image", payload)
    return {"object": "image.generation", "data": result.get("data", result)}


@router.post("/videos/generations")
async def generate_video(request: VideoRequest) -> dict[str, Any]:
    result = await _generate(VIDEO_URL, "Video", request.model_dump(exclude_none=True))
    return {"object": "video.generation", "data": result.get("data", result)}


@router.post("/audio/music/generations")
async def generate_music(request: MusicRequest) -> dict[str, Any]:
    result = await _generate(MUSIC_URL, "Music", request.model_dump(exclude_none=True))
    return {"object": "audio.music.generation", "data": result.get("data", result)}
