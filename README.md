# My AI API — v4

A self-hosted AI gateway for Flutter with ChatGPT-style chat, streaming, model routing, image/video/music generation, API-key authentication, rate limiting, health checks, and GPU deployment support.

## v4 upgrades

- OpenAI-compatible `POST /v1/chat/completions`
- SSE streaming chat
- `default`, `fast`, `reasoning`, and `coding` model aliases
- Model allow-list to prevent arbitrary Ollama model access
- Production API-key enforcement
- Request IDs and security response headers
- Rate limiting
- Public `/health` and `/ready` endpoints
- Safer error responses that do not expose backend exception details
- Image, video, and music gateway endpoints
- Flutter client support
- Docker/RunPod-friendly architecture

## Architecture

Flutter → HTTPS → FastAPI → Ollama / media GPU services

Ollama and the media server should remain private on the Docker network. Only the FastAPI service should be exposed publicly.

The repository stores code and configuration, not multi-gigabyte model weights. Models are downloaded onto persistent deployment storage when the services are started.

## Production configuration

Copy `.env.example` to `.env` and replace the API key with a long random secret. Keep `.env` out of Git.

Important variables:

```text
AI_API_KEY=your-long-random-secret
REQUIRE_API_KEY=true
ALLOWED_MODELS=qwen2.5:3b
OLLAMA_URL=http://ollama:11434
RATE_LIMIT_PER_MINUTE=60
```

For Docker Compose, the media gateway uses the internal service name:

```text
IMAGE_GENERATOR_URL=http://media:8100/v1/images/generations
VIDEO_GENERATOR_URL=http://media:8100/v1/videos/generations
MUSIC_GENERATOR_URL=http://media:8100/v1/audio/music/generations
```

## Endpoints

### Public operational checks

- `GET /health`
- `GET /ready`

### Protected AI API

- `GET /v1/models`
- `GET /v1/config`
- `POST /v1/chat/completions`
- `POST /chat`
- `GET /v1/media/capabilities`
- `POST /v1/images/generations`
- `POST /v1/videos/generations`
- `POST /v1/audio/music/generations`

Send the production key as:

```text
Authorization: Bearer YOUR_API_KEY
```

## Chat example

```bash
curl https://YOUR_DOMAIN/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"default",
    "messages":[{"role":"user","content":"Hello!"}],
    "stream":false
  }'
```

## Flutter

Use the public HTTPS API address in `AiService`:

```dart
final ai = AiService(baseUrl: 'https://YOUR_DOMAIN');
```

Do not embed a permanent production API key in a publicly distributed APK. For a public consumer app, add user authentication and server-side quotas/tokens.

## GPU deployment

The API is designed for a persistent GPU server such as a RunPod Pod. GitHub stores the source and deployment configuration; the GPU machine runs Docker, Ollama, and the media models.

Start the stack with:

```bash
docker compose up -d
```

Then verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Expose FastAPI through HTTPS/reverse proxy and keep ports for Ollama and the media service private.

## Media generation

The API is a gateway and does not magically create media without model runtimes. Actual image/video/music generation requires the configured GPU model services. Check the model licenses before using generated media commercially.
