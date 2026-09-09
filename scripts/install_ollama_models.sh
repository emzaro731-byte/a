#!/usr/bin/env bash
set -euo pipefail

# Installs Ollama when needed and pulls the configured text models.
# Run this on the persistent machine that will actually serve the AI.

OLLAMA_HOST_VALUE="${OLLAMA_HOST:-127.0.0.1:11434}"
DEFAULT_MODEL="${AI_MODEL:-qwen2.5:3b}"
FAST_MODEL="${AI_FAST_MODEL:-$DEFAULT_MODEL}"
REASONING_MODEL="${AI_REASONING_MODEL:-$DEFAULT_MODEL}"
CODING_MODEL="${AI_CODING_MODEL:-$DEFAULT_MODEL}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
fi

if ! pgrep -f 'ollama serve' >/dev/null 2>&1; then
  echo "Starting Ollama..."
  nohup ollama serve >/tmp/ollama.log 2>&1 &
fi

for i in {1..30}; do
  if curl -fsS "http://${OLLAMA_HOST_VALUE}/api/tags" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

for model in "$DEFAULT_MODEL" "$FAST_MODEL" "$REASONING_MODEL" "$CODING_MODEL"; do
  [ -n "$model" ] || continue
  echo "Pulling model: $model"
  ollama pull "$model"
done

echo "Ollama models are installed and ready."
