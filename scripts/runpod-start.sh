#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  echo "Missing .env. Create it from .env.example and set AI_API_KEY."
  exit 1
fi

command -v docker >/dev/null || { echo "Docker is required."; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose is required."; exit 1; }

echo "Starting AI API stack..."
docker compose up -d --build

echo "Waiting for FastAPI..."
for i in {1..60}; do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "AI API is online on port 8000."
    curl -fsS http://127.0.0.1:8000/health
    exit 0
  fi
  sleep 5
done

echo "API did not become healthy. Check: docker compose logs --tail=200 api"
exit 1
