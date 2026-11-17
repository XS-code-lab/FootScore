# FootScore v0.18 — PostgreSQL backup and isolated restore drill

**Scope:** an explicit custom-format backup script and an automated restore
drill on a NEW, disposable PostgreSQL database. No scheduled backups, off-site
storage, encryption of the archive at rest or production restore operation are
performed by this release. An archive containing user data is highly sensitive.

## Create a backup (trusted operator, outside Git)

Run from a machine that has a compatible PostgreSQL 16+ client with
`pg_dump` and `pg_restore` on PATH. Use `PGPASSFILE` with restrictive
permissions, or your secret-management system; do not put database passwords
in command arguments, source control or shell history.

```bash
export PGHOST=your-postgres-host
export PGPORT=5432
export PGUSER=footscore
export PGDATABASE=footscore
# Set PGPASSFILE=/secure/pgpass (mode 0600) using your secret manager
mkdir -m 700 -p /secure/footscore-backups
sh scripts/postgres_backup.sh /secure/footscore-backups/2026-11-18.dump
```

The script uses PostgreSQL custom format (`-Fc`), compression,
`--no-owner --no-acl`, secure file permissions (umask 077), and a
temporary file that is only published after `pg_dump` succeeds and
`pg_restore --list` can inspect the archive. It refuses to overwrite
an existing archive, including if a competing process creates it.
An interrupted or failed dump leaves no finished archive.

**A readable archive is not proof of a recoverable database.**
It may be truncated, have missing dependencies or reflect an old
schema. It still must be restored and validated periodically.

**Production security:** The custom-format archive is NOT encrypted.
Encrypt it before off-site storage, limit access, define retention,
rotate backup credentials, test restore keys, and monitor freshness.
Any encrypted one-time-code payloads within PostgreSQL additionally
need the original `EMAIL_QUEUE_KEY`; JWT validation requires
`JWT_SECRET`. A database archive alone is not enough to restore a
fully functional application. Provider API keys and other external
credentials are intentionally NOT included in the database archive.
Audit GDPR/privacy retention and secure deletion separately.

## Non-destructive restore check

Only restore to a **newly created, explicitly prefixed** database:

```bash
sh scripts/postgres_restore_drill.sh \
  /secure/footscore-backups/2026-11-18.dump \
  footscore_restore_drill_20261118
```

The target must start with `footscore_restore_` and use only letters,
numbers and underscores. It must not be the original `PGDATABASE`.
`createdb --template=template0` fails if a target database already
exists. The script **never drops or truncates an existing database** and
never uses `--clean`. It applies the archive with
`pg_restore --single-transaction --exit-on-error`, leaving source data
untouched. The target is NOT dropped automatically; operators should
inspect schema, row counts, API behavior and metadata and deliberately
remove disposable test databases afterward.

This helper is not a production failover procedure. Real disaster
recovery needs separate service deployment, DNS/network readiness,
secret restoration, integration checks, client reauthentication policies
and explicit operator approval.

## Real PostgreSQL CI roundtrip

The GitHub Actions integration job uses disposable PostgreSQL 16 and
Redis 7 containers. After normal service tests and a migration
downgrade/re-upgrade on that **test** database, it:

1. Seeds a synthetic completed football match, user, unpublished live
   match update, HMAC-only authentication token and email outbox row.
2. Starts a PostgreSQL 16 client container to run
   `scripts/postgres_backup.sh`, producing a custom archive in
   GitHub's temporary runner directory (never a public CI artifact).
3. Starts a client container to restore the archive into an entirely
   new database `footscore_restore_ci`.
4. Independently checks the **restored** Alembic head and every seeded
   object and relationship. Then verifies the **original** remains
   unchanged.
5. Confirms that rerunning the restore script refuses an already existing
   target. Drops only the deliberately named test restore database
   and deletes its local archive.

Backup scripts are additionally unit-tested for overwrite refusal,
missing backup directories, and unsafe target names. This CI test
never accesses a real FootScore deployment, server backups or actual
user data.

## Remaining work

No remote backup schedule, cloud object store, at-rest archive
encryption, offsite retention, alerting, RPO/RTO target, point-in-time
recovery (PITR) / WAL archiving, realistic large-data restore time
benchmark, high-availability failover or production-sized backup
validation is included. The run is a **small synthetic restore drill**
that proves basic compatibility with PostgreSQL 16, not disaster
recovery readiness at scale.
