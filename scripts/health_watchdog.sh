#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1/health}"
LOG_FILE="${WATCHDOG_LOG:-/var/log/destiny-ai-watchdog.log}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }

if curl -fsS --max-time 15 "$HEALTH_URL" >/dev/null; then
  log "API healthy."
  exit 0
fi

log "API health check failed; restarting application stack."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans
sleep 10

if curl -fsS --max-time 15 "$HEALTH_URL" >/dev/null; then
  log "API recovered after restart."
  exit 0
fi

log "API is still unhealthy after restart. Manual investigation required."
exit 1
