# My AI API — v5

A self-hosted AI platform for Flutter: ChatGPT-style chat, SSE streaming, model routing, multimodal message input, persistent conversations, long-term memory, private file context/RAG, safe tools, image/video/music gateways, API-key protection, rate limiting, health checks, and GPU deployment.

## v5 upgrades

- OpenAI-compatible `POST /v1/chat/completions`
- Streaming SSE and normal JSON responses
- `default`, `fast`, `reasoning`, `coding`, and `vision` model aliases
- Multimodal message payloads can pass through to compatible Ollama vision models
- Persistent SQLite conversations and messages
- Long-term user memory with create/list/delete endpoints
- Private UTF-8 text file upload and retrieval with lightweight relevance matching
- Safe built-in calculator and UTC-time tools
- Tool catalog and controlled tool execution endpoints
- Request IDs and security headers
- API-key enforcement and per-IP rate limiting
- Public `/health` and `/ready` operational checks
- Safer public errors
- Image/video/music generation gateway
- Persistent `/data` volume for conversations, memory, and files
- Docker/RunPod-friendly GPU architecture

## Architecture

Flutter → HTTPS → FastAPI → Ollama + private media workers → NVIDIA GPU

Ollama and the media server stay private on the Docker network. Only FastAPI should be exposed publicly.

Model weights are not stored in GitHub. They are downloaded onto persistent deployment storage.

## Core endpoints

### Operational

- `GET /health`
- `GET /ready`

### AI

- `GET /v1/models`
- `GET /v1/config`
- `GET /v1/tools`
- `POST /v1/tools/run`
- `POST /v1/chat/completions`
- `POST /chat`

### Memory and conversations

- `POST /v1/conversations`
- `GET /v1/conversations`
- `GET /v1/conversations/{id}/messages`
- `GET /v1/memories`
- `POST /v1/memories`
- `DELETE /v1/memories/{id}`

### Private files

- `POST /v1/files` — UTF-8 text, max 5 MB
- `GET /v1/files`
- `GET /v1/files/{id}`
- `DELETE /v1/files/{id}`

### Media

- `GET /v1/media/capabilities`
- `POST /v1/images/generations`
- `POST /v1/videos/generations`
- `POST /v1/audio/music/generations`

All protected endpoints use:

```text
Authorization: Bearer YOUR_API_KEY
```

## Chat example

```bash
curl https://YOUR_DOMAIN/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_API_KEY' \
  -H 'X-User-ID: demo-user' \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"default",
    "messages":[{"role":"user","content":"Hello!"}],
    "stream":false,
    "remember":true
  }'
```

## Persistent conversation

Create a conversation, then send its ID with chat requests:

```json
{"title":"My project chat"}
```

```json
{"conversation_id":"CONVERSATION_ID","messages":[{"role":"user","content":"Continue our project."}]}
```

## Flutter

```dart
final ai = AiService(baseUrl: 'https://YOUR_DOMAIN');
```

Do not ship a permanent production API key inside a public APK. The current `X-User-ID` mechanism is a lightweight development identity; for a public consumer product, replace it with verified JWT authentication and server-side user quotas.

## Docker/GPU

```bash
docker compose up -d --build
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

The API persists its SQLite database in the `ai_data` Docker volume. Ollama models, Hugging Face models, and generated media also use persistent volumes.

Use a persistent GPU server such as a RunPod Pod for this multi-service architecture. Put FastAPI behind HTTPS/reverse proxy and never expose Ollama `11434` or media `8100` directly to the internet.

## Media generation

The gateway routes requests to self-hosted model workers. Actual image/video/music generation still depends on the installed model runtimes, available VRAM, storage, and model licenses.
