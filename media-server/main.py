import os
import uuid
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

OUT = Path(os.getenv("MEDIA_OUTPUT_DIR", "/outputs"))
OUT.mkdir(parents=True, exist_ok=True)
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "THUDM/CogVideoX-2b")
MUSIC_MODEL = os.getenv("MUSIC_MODEL", "facebook/musicgen-small")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

app = FastAPI(title="Local Multimodal Generator", version="1.0.0")
_image_pipe = None
_video_pipe = None
_music_processor = None
_music_model = None

class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = None
    size: str = "1024x1024"
    n: int = Field(default=1, ge=1, le=2)

class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    duration: int = Field(default=5, ge=1, le=10)
    width: int = Field(default=720, ge=256, le=1280)
    height: int = Field(default=480, ge=256, le=1280)

class MusicRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    duration: int = Field(default=8, ge=1, le=30)


def load_image():
    global _image_pipe
    if _image_pipe is None:
        from diffusers import StableDiffusionXLPipeline
        _image_pipe = StableDiffusionXLPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=DTYPE)
        _image_pipe = _image_pipe.to(DEVICE)
    return _image_pipe


def load_video():
    global _video_pipe
    if _video_pipe is None:
        from diffusers import CogVideoXPipeline
        _video_pipe = CogVideoXPipeline.from_pretrained(VIDEO_MODEL, torch_dtype=DTYPE)
        _video_pipe.enable_model_cpu_offload()
    return _video_pipe


def load_music():
    global _music_processor, _music_model
    if _music_model is None:
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
        _music_processor = AutoProcessor.from_pretrained(MUSIC_MODEL)
        _music_model = MusicgenForConditionalGeneration.from_pretrained(
            MUSIC_MODEL, torch_dtype=DTYPE
        ).to(DEVICE)
    return _music_processor, _music_model

@app.get("/health")
def health():
    return {"status": "ok", "device": DEVICE, "cuda": torch.cuda.is_available()}

@app.get("/v1/media/capabilities")
def capabilities():
    return {"image": True, "video": True, "music": True, "device": DEVICE}

@app.post("/v1/images/generations")
def image(req: ImageRequest):
    pipe = load_image()
    try:
        w, h = [int(x) for x in req.size.lower().split("x")]
        result = pipe(prompt=req.prompt, negative_prompt=req.negative_prompt, width=w, height=h, num_images_per_prompt=req.n).images
        items = []
        for img in result:
            name = f"image-{uuid.uuid4().hex}.png"
            path = OUT / name
            img.save(path)
            items.append({"url": f"/v1/media/files/{name}"})
        return {"data": items}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

@app.post("/v1/videos/generations")
def video(req: VideoRequest):
    pipe = load_video()
    try:
        frames = pipe(prompt=req.prompt, num_frames=max(8, req.duration * 8), width=req.width, height=req.height).frames[0]
        name = f"video-{uuid.uuid4().hex}.mp4"
        path = OUT / name
        from diffusers.utils import export_to_video
        export_to_video(frames, str(path), fps=8)
        return {"data": [{"url": f"/v1/media/files/{name}"}]}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

@app.post("/v1/audio/music/generations")
def music(req: MusicRequest):
    processor, model = load_music()
    try:
        inputs = processor(text=[req.prompt], padding=True, return_tensors="pt").to(DEVICE)
        max_tokens = max(256, req.duration * 256)
        audio = model.generate(**inputs, max_new_tokens=max_tokens)
        name = f"music-{uuid.uuid4().hex}.wav"
        path = OUT / name
        import scipy.io.wavfile
        audio_np = audio[0, 0].detach().cpu().float().numpy()
        rate = model.config.audio_encoder.sampling_rate
        scipy.io.wavfile.write(str(path), rate=rate, data=audio_np)
        return {"data": [{"url": f"/v1/media/files/{name}"}]}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

@app.get("/v1/media/files/{name}")
def file(name: str):
    path = OUT / name
    if not path.is_file() or path.parent != OUT:
        raise HTTPException(404, "File not found")
    return FileResponse(path)
