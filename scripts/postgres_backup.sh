#!/bin/sh
# Safely create a PostgreSQL custom archive, without replacing prior backups.
# Requires pg_dump/pg_restore from a compatible PostgreSQL client installation.
set -eu
umask 077

if [ "$#" -ne 1 ] || [ -z "${1:-}" ]; then
    echo "Usage: PGHOST=... PGUSER=... PGDATABASE=... sh scripts/postgres_backup.sh /secure/path/backup.dump" >&2
    exit 2
fi
: "${PGHOST:?Set PGHOST explicitly}"
: "${PGUSER:?Set PGUSER explicitly}"
: "${PGDATABASE:?Set PGDATABASE explicitly}"

destination="$1"
if [ -e "$destination" ]; then
    echo "Refusing to overwrite an existing backup" >&2
    exit 2
fi
parent=$(dirname -- "$destination")
if [ ! -d "$parent" ]; then
    echo "Backup directory must already exist with restricted permissions" >&2
    exit 2
fi
tmp=$(mktemp "$parent/.footscore-backup.XXXXXXXX")
trap 'rm -f -- "$tmp"' EXIT HUP INT TERM

# Never supply passwords in process arguments or print connection strings.
# Use a locked-down .pgpass/PGPASSFILE or a secure PG* environment.
pg_dump --format=custom --compress=6 --no-owner --no-acl \
    --file="$tmp" "$PGDATABASE"
# Checking the archive directory detects common empty/corrupt output.
pg_restore --list "$tmp" >/dev/null
if [ ! -s "$tmp" ]; then
    echo "Backup is empty" >&2
    exit 1
fi

# Hard-link is an atomic create-if-absent on the same filesystem: no overwrite
# when another backup process selected this exact destination concurrently.
ln "$tmp" "$destination"
rm -f -- "$tmp"
trap - EXIT HUP INT TERM
echo "PostgreSQL backup saved to requested destination; secure it off-host."
