#!/usr/bin/env bash
set -euo pipefail

DUMP_FILE=${1:?"Usage: scripts/db_restore.sh <dump-file> [database-name]"}
PGDATABASE=${2:-${DATABASE_NAME:-pgbot1010}}

if [ ! -f "$DUMP_FILE" ]; then
  echo "Dump file $DUMP_FILE does not exist" >&2
  exit 1
fi

PGHOST=${DATABASE_HOST:-localhost}
PGPORT=${DATABASE_PORT:-5432}
PGUSER=${DATABASE_USER:-pgbot1010_user}
PGPASSWORD=${DATABASE_PASSWORD:-}
export PGPASSWORD

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "pg_restore not found in PATH" >&2
  exit 1
fi

createdb --host="$PGHOST" --port="$PGPORT" --username="$PGUSER" --echo "$PGDATABASE" 2>/dev/null || true

pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --host="$PGHOST" \
  --port="$PGPORT" \
  --username="$PGUSER" \
  --dbname="$PGDATABASE" \
  "$DUMP_FILE"

echo "Database $PGDATABASE restored from $DUMP_FILE"
