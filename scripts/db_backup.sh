#!/bin/sh
#
# Nightly Postgres backup (portable — works for compose Postgres AND cloud
# Postgres such as Supabase/managed RDS, since it only needs a connection
# string). Written in POSIX sh so it runs on Alpine/BSD/systemd hosts and in
# the postgres:*-alpine containers.
#
#   BACKUP_DATABASE_URL  required. The DB you want to dump.
#     For compose:  postgresql://halohubx:${POSTGRES_PASSWORD}@db:5432/halohubx
#     For Supabase: postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres
#   BACKUP_DIR      where dumps go (default ./backups)
#   BACKUP_KEEP     how many newest dumps to retain (default 14)
#
# Outputs: $BACKUP_DIR/<dbname>-<UTC timestamp>-<pid>.sql.gz
# Restore a dump with scripts/db_restore.sh — see DEPLOY_PROFEZZO.md runbooks.
#
set -e

: "${BACKUP_DATABASE_URL:?BACKUP_DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

mkdir -p "$BACKUP_DIR"

# Parse the DB name out of the URL for the file name (fall back to "db").
db_name="db"
case "$BACKUP_DATABASE_URL" in
    *://*/) ;;        # trailing slash, empty db name
esac
# Extract the path segment after the last '/' and strip anything before '?'.
raw_path="${BACKUP_DATABASE_URL#*://*/}"
raw_path="${raw_path%%\?*}"
if [ -n "$raw_path" ]; then
    db_name="$raw_path"
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$BACKUP_DIR/${db_name}-${stamp}-$$.sql.gz"

echo "[backup] dumping $db_name -> $out"
pg_dump "$BACKUP_DATABASE_URL" --no-owner --no-privileges | gzip > "$out"
echo "[backup] done: $(du -h "$out" | cut -f1)"

# Prune old backups, keep the newest BACKUP_KEEP.
n=0
for f in $(ls -1t "$BACKUP_DIR"/*.sql.gz 2>/dev/null); do
    n=$((n + 1))
    if [ "$n" -gt "$BACKUP_KEEP" ]; then
        echo "[backup] pruning $f"
        rm -f "$f"
    fi
done
remaining=$(ls -1 "$BACKUP_DIR"/*.sql.gz 2>/dev/null | wc -l)
echo "[backup] retained $remaining dump(s)"
