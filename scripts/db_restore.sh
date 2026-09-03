#!/bin/sh
#
# Restore a PostgreSQL dump into a target database (disaster recovery).
# Works for compose + cloud (Supabase) targets. POSIX sh so it runs on
# Alpine/BSD and in postgres:*-alpine containers.
#
#   RESTORE_DATABASE_URL  required. Where to restore into. This should be a
#                         FRESH/EMPTY database — restoring into an existing
#                         populated DB will merge/conflict and is discouraged.
#   DUMP_FILE             required. Path to the .sql or .sql.gz dump.
#
# Recommended DR flow:
#   1. Create an empty target database (e.g. CREATE DATABASE foo).
#   2. Run:  RESTORE_DATABASE_URL=... scripts/db_restore.sh dump.sql.gz
#   3. Run init_db migrations on the restored DB, then point the app at it.
#
set -e

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required}"
if [ "$#" -lt 1 ]; then
    echo "usage: db_restore.sh <dump.sql[.gz]>" >&2
    exit 1
fi
DUMP_FILE="$1"

if [ ! -f "$DUMP_FILE" ]; then
    echo "[restore] dump not found: $DUMP_FILE" >&2
    exit 1
fi

echo "[restore] loading $DUMP_FILE into RESTORE_DATABASE_URL (target must be empty)"
if [ "${DUMP_FILE##*.}" = "gz" ]; then
    gzip -dc "$DUMP_FILE"
else
    cat "$DUMP_FILE"
fi | psql "$RESTORE_DATABASE_URL" -v ON_ERROR_STOP=1 -q 1>/dev/null
echo "[restore] done"
