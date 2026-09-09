#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/destiny-ai}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-a-postgres-1}"
POSTGRES_USER="${POSTGRES_USER:-supabase}"
POSTGRES_DB="${POSTGRES_DB:-supabase}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/${POSTGRES_DB}_${STAMP}.sql.gz"

echo "Creating PostgreSQL backup: $FILE"
docker exec "$POSTGRES_CONTAINER" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges | gzip > "$FILE"
test -s "$FILE"
chmod 600 "$FILE"

find "$BACKUP_DIR" -type f -name '*.sql.gz' -mtime "+$RETENTION_DAYS" -delete

echo "Backup complete."
