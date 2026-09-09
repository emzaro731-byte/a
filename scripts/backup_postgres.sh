#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/destiny-ai}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-supabase}"
POSTGRES_DB="${POSTGRES_DB:-supabase}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/${POSTGRES_DB}_${STAMP}.sql.gz"
TMP_FILE="${FILE}.tmp"
trap 'rm -f "$TMP_FILE"' EXIT

echo "Creating PostgreSQL backup: $FILE"
docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges | gzip > "$TMP_FILE"
test -s "$TMP_FILE"
mv "$TMP_FILE" "$FILE"
chmod 600 "$FILE"

find "$BACKUP_DIR" -type f -name '*.sql.gz' -mtime "+$RETENTION_DAYS" -delete

echo "Backup complete: $FILE"
