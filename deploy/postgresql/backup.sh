#!/usr/bin/env bash
set -euo pipefail

umask 077

: "${COMMISSIONS_POSTGRES_BACKUP_DSN:?Configure COMMISSIONS_POSTGRES_BACKUP_DSN without a password}"
: "${PGPASSFILE:?Configure PGPASSFILE with the PostgreSQL backup credentials}"

if [[ ! -f "$PGPASSFILE" ]]; then
    echo "PGPASSFILE does not exist: $PGPASSFILE" >&2
    exit 1
fi

backup_dir="${COMMISSIONS_BACKUP_DIR:-/var/backups/servimeters-comisiones}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_path="$backup_dir/comisiones-$timestamp.dump"
temporary_path="$final_path.partial"

mkdir -p "$backup_dir"
trap 'rm -f "$temporary_path"' EXIT

pg_dump \
    --dbname="$COMMISSIONS_POSTGRES_BACKUP_DSN" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --file="$temporary_path"

pg_restore --list "$temporary_path" >/dev/null
mv "$temporary_path" "$final_path"
trap - EXIT

printf '%s\n' "$final_path"
