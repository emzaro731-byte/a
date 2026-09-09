#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/backup.sql.gz"
  exit 2
fi

BACKUP_FILE="$1"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-a-postgres-1}"
POSTGRES_USER="${POSTGRES_USER:-supabase}"
POSTGRES_DB="${POSTGRES_DB:-supabase}"

test -s "$BACKUP_FILE"
echo "WARNING: this restores into $POSTGRES_DB and can overwrite existing data."
read -r -p "Type RESTORE to continue: " CONFIRM
[ "$CONFIRM" = "RESTORE" ]

gzip -dc "$BACKUP_FILE" | docker exec -i "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
echo "Restore complete."
