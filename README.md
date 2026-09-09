# My AI API — v3

A self-hosted AI API for Flutter with chat, image, video, and music generation. Chat runs through Ollama; media requests are routed to local/self-hosted generator services, so the API can stay independent of a commercial AI provider.

## Features

- OpenAI-style `/v1/chat/completions`
- Streaming chat with SSE
- Fast/reasoning/coding model routing
- Image generation
- Video generation
- Music generation
- Media capability discovery
- API-key authentication and rate limiting
- Flutter client for chat + media

## Architecture

Flutter → HTTPS → FastAPI

- Chat → Ollama → local open model
- Images → local image generator
- Video → local video generator
- Music → local music generator

The media layer is a gateway: you choose which self-hosted generator/model runs behind each URL. The repository does not bundle multi-gigabyte model weights.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Configure `.env.example` before production deployment.

## API

### Chat

`POST /v1/chat/completions`

### Image

`POST /v1/images/generations`

```json
{"prompt":"cinematic African city at sunset","size":"1024x1024","n":1}
```

### Video

`POST /v1/videos/generations`

```json
{"prompt":"a futuristic city above the clouds","duration":5,"width":1024,"height":576}
```

### Music

`POST /v1/audio/music/generations`

```json
{"prompt":"Afrobeats instrumental with warm guitar and deep bass","duration":30,"instrumental":true,"bpm":105}
```

### Capabilities

`GET /v1/media/capabilities`

This tells the Flutter app which media generators are configured.

## Connect local generators

Set these variables:

```text
IMAGE_GENERATOR_URL=...
VIDEO_GENERATOR_URL=...
MUSIC_GENERATOR_URL=...
MEDIA_GENERATOR_TOKEN=...
```

Each configured generator receives the JSON request from the API and should return JSON containing a `data` field or a JSON object with the generated asset URL/result. This keeps the main API independent of a specific image/video/music framework.

For a fully local deployment, run the generator models on a GPU server and point the three URLs to those local services. Image/video/music models can require substantially more VRAM and storage than the chat model.

## Flutter

`flutter/ai_service.dart` now includes:

- `chat()`
- `chatStream()`
- `models()`
- `mediaCapabilities()`
- `generateImage()`
- `generateVideo()`
- `generateMusic()`

Example:

```dart
final ai = AiService(baseUrl: 'https://your-domain.com');
final image = await ai.generateImage(
  prompt: 'cinematic Lagos skyline at sunset',
);
final video = await ai.generateVideo(
  prompt: 'a futuristic city flying through clouds',
  duration: 8,
);
final music = await ai.generateMusic(
  prompt: 'Afrobeats instrumental with warm guitar and deep bass',
  duration: 30,
);
```

## Important

Adding endpoints does not itself create the media models. Actual generation requires image, video, and music model runtimes connected to the three generator URLs. This design lets you use self-hosted/open models instead of embedding commercial provider keys in your Flutter APK.

## Security

- Use HTTPS in production.
- Keep `AI_API_KEY` and `MEDIA_GENERATOR_TOKEN` on the server.
- Never ship permanent server secrets in the APK.
- Restrict CORS and rate limits.
- Add authentication and per-user quotas before opening generation to the public.
