# My AI API

A self-hosted AI API for Flutter. It uses FastAPI as the public API and Ollama as the local model runtime. No OpenAI API key is required.

## Architecture

Flutter → FastAPI → Ollama → Qwen/Gemma/Llama

## Run locally

1. Install Ollama: https://ollama.com
2. Start a model, for example:

```bash
ollama pull qwen2.5:3b
ollama serve
```

3. Install Python dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. Start the API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

API docs: `http://localhost:8000/docs`

## Endpoints

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /chat`

## Example

```bash
curl http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:3b","messages":[{"role":"user","content":"Hello"}]}'
```

## Flutter

Set your API URL in the Flutter app and POST JSON to `/v1/chat/completions`. The response follows the familiar OpenAI-style chat-completions shape, but the model is served by your own server.

## Production

Put the API behind HTTPS, set `AI_API_KEY`, restrict CORS, and run Ollama on a machine with enough RAM/GPU for the selected model. Never put the server API key in a public Flutter APK; use user authentication or a short-lived backend token instead.
