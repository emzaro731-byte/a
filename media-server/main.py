import base64
import io
import os
import uuid
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

OUT = Path(os.getenv("MEDIA_OUTPUT_DIR", "/outputs"))
OUT.mkdir(parents=True, exist_ok=True)
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "THUDM/CogVideoX-2b")
MUSIC_MODEL = os.getenv("MUSIC_MODEL", "facebook/musicgen-small")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

app = FastAPI(title="Local Multimodal Generator", version="1.1.0")
_image_pipe = None
_img2img_pipe = None
_video_pipe = None
_music_processor = None
_music_model = None

STYLE_PRESETS = {
    "none": "",
    "photorealistic": "photorealistic, natural lighting, realistic details, high dynamic range",
    "cinematic": "cinematic photography, dramatic lighting, film still, shallow depth of field",
    "anime": "high quality anime illustration, detailed line art, vibrant colors",
    "digital-art": "premium digital art, highly detailed, polished composition",
    "3d": "high-end 3D render, physically based materials, studio quality",
    "fantasy": "epic fantasy art, atmospheric lighting, intricate details",
    "cyberpunk": "cyberpunk aesthetic, neon lighting, futuristic city atmosphere",
}

class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = None
    size: str = "1024x1024"
    n: int = Field(default=1, ge=1, le=2)
    style: str = "none"
    seed: int | None = None

class ImageEditRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    image: str = Field(min_length=10)
    negative_prompt: str | None = None
    strength: float = Field(default=0.65, ge=0.05, le=0.95)
    style: str = "none"
    seed: int | None = None

class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    duration: int = Field(default=5, ge=1, le=10)
    width: int = Field(default=720, ge=256, le=1280)
    height: int = Field(default=480, ge=256, le=1280)

class MusicRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    duration: int = Field(default=8, ge=1, le=30)


def styled_prompt(prompt: str, style: str) -> str:
    preset = STYLE_PRESETS.get(style.lower(), "")
    return f"{prompt}, {preset}" if preset else prompt


def generator(seed: int | None):
    return torch.Generator(device=DEVICE).manual_seed(seed) if seed is not None else None


def save_image(img: Image.Image) -> str:
    name = f"image-{uuid.uuid4().hex}.png"
    path = OUT / name
    img.save(path, format="PNG")
    return f"/v1/media/files/{name}"


def decode_image(value: str) -> Image.Image:
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    try:
        raw = base64.b64decode(value, validate=True)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        raise HTTPException(400, "image must be a valid base64-encoded image") from exc


def load_image():
    global _image_pipe
    if _image_pipe is None:
        from diffusers import StableDiffusionXLPipeline
        _image_pipe = StableDiffusionXLPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=DTYPE)
        _image_pipe = _image_pipe.to(DEVICE)
    return _image_pipe


def load_img2img():
    global _img2img_pipe
    if _img2img_pipe is None:
        from diffusers import StableDiffusionXLImg2ImgPipeline
        _img2img_pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=DTYPE)
        _img2img_pipe = _img2img_pipe.to(DEVICE)
    return _img2img_pipe


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
        _music_model = MusicgenForConditionalGeneration.from_pretrained(MUSIC_MODEL, torch_dtype=DTYPE).to(DEVICE)
    return _music_processor, _music_model

@app.get("/health")
def health():
    return {"status": "ok", "device": DEVICE, "cuda": torch.cuda.is_available(), "image_model": IMAGE_MODEL}

@app.get("/v1/media/capabilities")
def capabilities():
    return {
        "image": True,
        "image_edit": True,
        "styles": sorted(STYLE_PRESETS.keys()),
        "video": True,
        "music": True,
        "device": DEVICE,
    }

@app.post("/v1/images/generations")
def image(req: ImageRequest):
    pipe = load_image()
    try:
        w, h = [int(x) for x in req.size.lower().split("x")]
        if not (256 <= w <= 1536 and 256 <= h <= 1536):
            raise ValueError("size must be between 256 and 1536 pixels per side")
        prompt = styled_prompt(req.prompt, req.style)
        kwargs = {
            "prompt": prompt,
            "negative_prompt": req.negative_prompt,
            "width": w,
            "height": h,
            "num_images_per_prompt": req.n,
        }
        if req.seed is not None:
            kwargs["generator"] = generator(req.seed)
        result = pipe(**kwargs).images
        return {"data": [{"url": save_image(img)} for img in result]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

@app.post("/v1/images/edits")
def edit_image(req: ImageEditRequest):
    pipe = load_img2img()
    try:
        source = decode_image(req.image)
        source.thumbnail((1536, 1536))
        prompt = styled_prompt(req.prompt, req.style)
        kwargs = {
            "prompt": prompt,
            "image": source,
            "strength": req.strength,
            "negative_prompt": req.negative_prompt,
        }
        if req.seed is not None:
            kwargs["generator"] = generator(req.seed)
        result = pipe(**kwargs).images[0]
        return {"data": [{"url": save_image(result)}]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

@app.get("/v1/images/styles")
def image_styles():
    return {"styles": sorted(STYLE_PRESETS.keys())}

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
