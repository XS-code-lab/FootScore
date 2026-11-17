#!/bin/sh
# Restore ONLY into a newly created disposable database with a fixed prefix.
# NEVER runs --clean or --drop against a source or existing target database.
set -eu

if [ "$#" -ne 2 ]; then
    echo "Usage: PGHOST=... PGUSER=... PGDATABASE=source sh scripts/postgres_restore_drill.sh ARCHIVE footscore_restore_NAME" >&2
    exit 2
fi
: "${PGHOST:?Set PGHOST explicitly}"
: "${PGUSER:?Set PGUSER explicitly}"
: "${PGDATABASE:?Set source PGDATABASE explicitly}"
archive="$1"
target="$2"
case "$target" in
    footscore_restore_*)
        # Names are passed only as command arguments, never interpolated
        # into SQL. Still restrict the allowed set and maximum length.
        ;;
    *)
        echo "Restore target must begin with footscore_restore_" >&2
        exit 2
        ;;
esac
case "$target" in
    *[!a-zA-Z0-9_]* | footscore_restore_)
        echo "Invalid disposable restore target name" >&2
        exit 2
        ;;
esac
if [ "${#target}" -gt 48 ] || [ "$target" = "$PGDATABASE" ]; then
    echo "Unsafe or overly long restore database name" >&2
    exit 2
fi
if [ ! -r "$archive" ] || [ ! -s "$archive" ]; then
    echo "Backup archive is missing or empty" >&2
    exit 2
fi

pg_restore --list "$archive" >/dev/null
# createdb FAILS if target already exists, so an existing DB is never
# destroyed, truncated or modified by this helper.
createdb --maintenance-db=postgres --template=template0 "$target"
pg_restore --dbname="$target" --single-transaction --exit-on-error \
    --no-owner --no-acl "$archive"
echo "Restore completed in a NEW disposable database; verify independently."
