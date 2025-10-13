#!/usr/bin/env bash
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=${DATABASE_BACKUP_DIR:-backups}
OUTPUT_DIR=${1:-$BACKUP_DIR}
FILENAME=${2:-${DATABASE_NAME:-pgbot1010}_${TIMESTAMP}.dump}

mkdir -p "$OUTPUT_DIR"
BACKUP_PATH=$(realpath "$OUTPUT_DIR")/"$FILENAME"

PGHOST=${DATABASE_HOST:-localhost}
PGPORT=${DATABASE_PORT:-5432}
PGUSER=${DATABASE_USER:-pgbot1010_user}
PGDATABASE=${DATABASE_NAME:-pgbot1010}
PGPASSWORD=${DATABASE_PASSWORD:-}
export PGPASSWORD

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "pg_dump not found in PATH" >&2
  exit 1
fi

pg_dump \
  --format=custom \
  --no-owner \
  --no-privileges \
  --host="$PGHOST" \
  --port="$PGPORT" \
  --username="$PGUSER" \
  --file="$BACKUP_PATH" \
  "$PGDATABASE"

echo "Backup created at $BACKUP_PATH"
