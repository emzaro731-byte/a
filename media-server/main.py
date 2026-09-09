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

app = FastAPI(title="Local Multimodal Generator", version="2.0.0")
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
    "watercolor": "delicate watercolor painting, paper texture, soft pigment washes",
    "oil-painting": "classical oil painting, rich brushwork, textured canvas",
    "comic": "professional comic-book illustration, bold ink lines, dynamic composition",
}

class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    size: str = Field(default="1024x1024", pattern=r"^\d{3,4}x\d{3,4}$")
    n: int = Field(default=1, ge=1, le=4)
    style: str = "none"
    seed: int | None = None
    steps: int = Field(default=30, ge=10, le=80)
    guidance_scale: float = Field(default=7.5, ge=1, le=20)

class ImageEditRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    image: str = Field(min_length=10, description="Base64 data URL or base64 image")
    negative_prompt: str | None = Field(default=None, max_length=4000)
    strength: float = Field(default=0.65, ge=0.05, le=0.95)
    style: str = "none"
    seed: int | None = None
    steps: int = Field(default=30, ge=10, le=80)
    guidance_scale: float = Field(default=7.5, ge=1, le=20)
    n: int = Field(default=1, ge=1, le=4)

class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    duration: int = Field(default=5, ge=1, le=10)
    width: int = Field(default=720, ge=256, le=1280)
    height: int = Field(default=480, ge=256, le=1280)
    seed: int | None = None

class MusicRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    duration: int = Field(default=8, ge=1, le=30)


def styled_prompt(prompt: str, style: str) -> str:
    preset = STYLE_PRESETS.get(style.lower(), "")
    return f"{prompt}, {preset}" if preset else prompt


def generator(seed: int | None):
    return torch.Generator(device=DEVICE).manual_seed(seed) if seed is not None else None


def save_image(img: Image.Image) -> dict[str, str]:
    name = f"image-{uuid.uuid4().hex}.png"
    path = OUT / name
    img.save(path, format="PNG")
    return {"url": f"/v1/media/files/{name}", "id": name, "mime_type": "image/png"}


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
        if DEVICE == "cuda":
            _image_pipe.enable_attention_slicing()
    return _image_pipe


def load_img2img():
    global _img2img_pipe
    if _img2img_pipe is None:
        from diffusers import StableDiffusionXLImg2ImgPipeline
        _img2img_pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=DTYPE)
        _img2img_pipe = _img2img_pipe.to(DEVICE)
        if DEVICE == "cuda":
            _img2img_pipe.enable_attention_slicing()
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
    return {"status": "ok", "device": DEVICE, "cuda": torch.cuda.is_available(), "image_model": IMAGE_MODEL, "version": "2.0.0"}

@app.get("/v1/media/capabilities")
def capabilities():
    return {"image": True, "image_edit": True, "image_variations": True, "styles": True, "video": True, "music": True, "device": DEVICE, "local_only": True}

@app.get("/v1/images/models")
def image_models():
    return {"data": [{"id": IMAGE_MODEL, "type": "text-to-image,image-to-image", "local": True}]}

@app.get("/v1/images/styles")
def image_styles():
    return {"data": [{"id": key, "prompt_suffix": value} for key, value in STYLE_PRESETS.items()]}

@app.post("/v1/images/generations")
def image(req: ImageRequest):
    pipe = load_image()
    try:
        w, h = [int(x) for x in req.size.lower().split("x")]
        if not (256 <= w <= 1536 and 256 <= h <= 1536):
            raise ValueError("size must be between 256 and 1536 pixels per side")
        kwargs = {
            "prompt": styled_prompt(req.prompt, req.style),
            "negative_prompt": req.negative_prompt,
            "width": w,
            "height": h,
            "num_images_per_prompt": req.n,
            "num_inference_steps": req.steps,
            "guidance_scale": req.guidance_scale,
        }
        if req.seed is not None:
            kwargs["generator"] = generator(req.seed)
        result = pipe(**kwargs).images
        return {"object": "image.generation", "data": [save_image(img) for img in result], "model": IMAGE_MODEL, "style": req.style, "seed": req.seed}
    except Exception as exc:
        raise HTTPException(500, f"Image generation failed: {exc}") from exc

@app.post("/v1/images/edits")
def edit_image(req: ImageEditRequest):
    pipe = load_img2img()
    try:
        source = decode_image(req.image)
        source.thumbnail((1536, 1536))
        kwargs = {
            "prompt": styled_prompt(req.prompt, req.style),
            "image": source,
            "strength": req.strength,
            "negative_prompt": req.negative_prompt,
            "num_images_per_prompt": req.n,
            "num_inference_steps": req.steps,
            "guidance_scale": req.guidance_scale,
        }
        if req.seed is not None:
            kwargs["generator"] = generator(req.seed)
        result = pipe(**kwargs).images
        return {"object": "image.edit", "data": [save_image(img) for img in result], "model": IMAGE_MODEL, "style": req.style, "seed": req.seed}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Image editing failed: {exc}") from exc

@app.post("/v1/images/variations")
def image_variation(req: ImageEditRequest):
    req.strength = min(req.strength, 0.55)
    return edit_image(req)

@app.post("/v1/videos/generations")
def video(req: VideoRequest):
    pipe = load_video()
    try:
        kwargs = {"prompt": req.prompt, "num_frames": max(8, req.duration * 8), "width": req.width, "height": req.height}
        if req.seed is not None:
            kwargs["generator"] = generator(req.seed)
        frames = pipe(**kwargs).frames[0]
        name = f"video-{uuid.uuid4().hex}.mp4"
        from diffusers.utils import export_to_video
        export_to_video(frames, str(OUT / name), fps=8)
        return {"data": [{"url": f"/v1/media/files/{name}", "id": name}]}
    except Exception as exc:
        raise HTTPException(500, f"Video generation failed: {exc}") from exc

@app.post("/v1/audio/music/generations")
def music(req: MusicRequest):
    processor, model = load_music()
    try:
        inputs = processor(text=[req.prompt], padding=True, return_tensors="pt").to(DEVICE)
        audio = model.generate(**inputs, max_new_tokens=max(256, req.duration * 256))
        name = f"music-{uuid.uuid4().hex}.wav"
        import scipy.io.wavfile
        audio_np = audio[0, 0].detach().cpu().float().numpy()
        scipy.io.wavfile.write(str(OUT / name), rate=model.config.audio_encoder.sampling_rate, data=audio_np)
        return {"data": [{"url": f"/v1/media/files/{name}", "id": name}]}
    except Exception as exc:
        raise HTTPException(500, f"Music generation failed: {exc}") from exc

@app.get("/v1/media/files/{name}")
def file(name: str):
    path = OUT / name
    if not path.is_file() or path.parent != OUT:
        raise HTTPException(404, "File not found")
    return FileResponse(path)
