# My AI API — v2

A self-hosted AI API for Flutter. It provides an OpenAI-compatible interface while the actual model runs through Ollama on your own machine/server. You do not need an OpenAI API key.

## What was upgraded

- OpenAI-style `/v1/chat/completions`
- Streaming responses with Server-Sent Events
- Model routing: `default`, `fast`, `reasoning`, `coding`
- Configurable system prompt
- Automatic token/latency usage metadata when Ollama provides it
- API-key authentication
- Per-IP rate limiting
- Health and model discovery endpoints
- Flutter client with streaming and cancellation support

## Architecture

Flutter → HTTPS → FastAPI → Ollama → local open model

## Run locally

1. Install Ollama from https://ollama.com
2. Pull a model. For a small local setup:

```bash
ollama pull qwen2.5:3b
ollama serve
```

For a stronger server, configure `AI_MODEL`, `AI_REASONING_MODEL`, and `AI_CODING_MODEL` to models that your hardware can run. Bigger models generally need substantially more RAM/VRAM.

3. Install Python dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. Configure environment variables using `.env.example`.
5. Start the API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Interactive docs: `http://localhost:8000/docs`

## API

- `GET /health` — service/model health
- `GET /v1/models` — models available in Ollama
- `GET /v1/config` — configured model aliases
- `POST /v1/chat/completions` — normal or streaming chat
- `POST /chat` — compatibility endpoint

### Model aliases

Send `"model":"fast"`, `"reasoning"`, or `"coding"` and the API maps that alias to the configured local model. You can also send the exact Ollama model name.

### Streaming

Set `"stream": true` on `/v1/chat/completions`. The API returns SSE chunks compatible with common OpenAI-style clients.

## Flutter

`flutter/ai_service.dart` supports normal requests, model discovery, streaming generation, configurable temperature/max tokens, API-key authentication, and request cancellation.

Example:

```dart
final ai = AiService(baseUrl: 'https://your-domain.com');
final reply = await ai.chat(
  model: 'reasoning',
  messages: [
    {'role': 'user', 'content': 'Solve this problem step by step.'},
  ],
);
```

For live typing:

```dart
await for (final token in ai.chatStream(messages: messages)) {
  // Append token to the current assistant message.
}
```

## Making it genuinely powerful

The API layer does not magically make a small model equal to a frontier model. Capability depends mainly on the model, inference hardware, context, and additional tools. This architecture lets you upgrade those pieces without rewriting your Flutter app.

Recommended production upgrades are a strong licensed/open model, GPU inference, retrieval-augmented generation (RAG), persistent conversation memory, controlled tool calling, moderation, and user authentication.

## Security

- Use HTTPS in production.
- Set a strong random `AI_API_KEY`.
- Restrict `CORS_ORIGINS` instead of `*`.
- Do not embed a permanent server API key inside a public APK.
- Put authentication/rate limiting in front of the API for public users.
- Never commit `.env` or real secrets.
