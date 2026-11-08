#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 /path/to/comisiones-backup.dump" >&2
    exit 2
fi

backup_path="$1"
if [[ ! -f "$backup_path" ]]; then
    echo "Backup does not exist: $backup_path" >&2
    exit 1
fi

pg_restore --list "$backup_path" >/dev/null
printf 'Backup archive is readable: %s\n' "$backup_path"
