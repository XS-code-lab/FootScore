# FootScore v0.28 — separating PostgreSQL migration and runtime privileges

The development Compose setup currently uses one privileged database account.
**This release does NOT silently migrate existing production credentials or
revoke access.** It provides a read-only audit and a test of a **real,
separately authenticated PostgreSQL LOGIN runtime user** inside CI.

## Why two roles?

A deployment needs at least two credential identities:

- **Migration owner / operator:** runs Alembic, database schema DDL,
  backups, provisioning, security reviews and migrations. Its credentials
  are NOT present in the running API or scheduled ingestion processes.
- **Application runtime:** performs ordinary data reads/writes needed by
  API, ingestion and email/notification queues. Must not be a superuser,
  CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS, have database/schema CREATE,
  own application tables, or inherit the migration owner's privileges.
  It must not receive TRUNCATE, REFERENCES or TRIGGER on tables.

A runtime account with only SELECT is not sufficient: FootScore
registration, rate limiting, provider quotas, live-update outboxes and
notification jobs all require some INSERT/UPDATE/DELETE operations.

For the **initial audited baseline**, grant table
`SELECT, INSERT, UPDATE, DELETE` and sequence `USAGE, SELECT`
under the `public` schema. This covers the current application
features, but it remains broad access to all application tables, including
security and account records. This is *not* a final per-service least
privilege, multi-tenant RLS policy or external security certification.

## Operator-only provisioning outline

Use a controlled change on a **separate staging database** first.
Role names below are examples, not secret values. Provision LOGIN
credentials via workload identity, managed database user or your
secret manager; **do not paste database passwords into shell history,
Git or an SQL migration**.

An authorized database administrator must ensure
`footscore_migrator` can own/create/migrate application objects,
and `footscore_app` is an independent LOGIN role with
`NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
NOINHERIT`. Apply role membership and schema ownership separately
under an approved change plan. Do not use a blind `REASSIGN OWNED`
or ALTER DATABASE OWNER against a production database.

After migrating schema with the migration-owner credentials, have
the object-owning migrator explicitly grant runtime permissions:

```sql
-- Review public schema's existing PUBLIC privileges separately.
-- These are GRANT examples only: no role/password creation.
GRANT USAGE ON SCHEMA public TO footscore_app;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public TO footscore_app;
GRANT USAGE, SELECT
    ON ALL SEQUENCES IN SCHEMA public TO footscore_app;

-- Apply future-object grants as the SAME role that owns future
-- Alembic-created objects (shown here as footscore_migrator).
ALTER DEFAULT PRIVILEGES FOR ROLE footscore_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO footscore_app;
ALTER DEFAULT PRIVILEGES FOR ROLE footscore_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO footscore_app;
```

Use database-level `CONNECT` grants with appropriate revocation of
unnecessary `PUBLIC` CONNECT access, and ensure no other group
membership grants the runtime role schema CREATE or owner powers.
Review existing database/schema default ACLs: granting `USAGE`
does **not** automatically revoke CREATE previously granted to PUBLIC.

**Important:** `ALTER DEFAULT PRIVILEGES FOR ROLE ...` applies only
to objects created by that owner. If another deployment role or a
different owner runs migrations, grant rules must be reviewed and
maintained for that creator too.

If tables already belong to a different role, the migrator must either
own them or have adequate ALTER rights; the runtime role should not be
used to run Alembic. Use separate Kubernetes/Compose/secret profiles
for migrations versus API/scheduler/worker services.

## Runtime permission audit (read-only)

Run with the **actual runtime DATABASE_URL**, never the migration URL:

```bash
python -m app.db_role_audit \
  --migration-role footscore_migrator --strict
```

This command only SELECTs PostgreSQL catalog and privilege metadata.
It never changes roles, grants, rows, database settings or backups.
It emits aggregate counts and safe boolean flags, not credentials,
role names, row payloads or actual ACL definitions.

Strict exit status is **0** when the configured runtime policy passes,
**2** when it fails, or **1** for a database/query failure.
The audit checks login status, admin role flags, absence of schema
and database CREATE, distinct/non-member migrator identity, absence
of owned public application tables, all table CRUD privileges,
absence of elevated table TRUNCATE/REFERENCES/TRIGGER privileges,
and sequence USAGE. It fails on empty or malformed results.

A passing report is a **snapshot of the observed database account's
current effective privileges**. It does NOT check every SQL command,
record-level permissions, table-specific sensitive data exposure,
RLS policy correctness, connection TLS or cluster-global role/credential
recoverability. Carefully protect the DATABASE_URL itself.

## What real CI proves

The disposable PostgreSQL 16 integration job still completes the
encrypted backup and content/catalog/security comparisons first.
After its restored FastAPI smoke it then:

1. Audits the existing privileged migration owner and confirms it
   **fails** the runtime security gate.
2. Creates a random-password, CI-only `LOGIN` PostgreSQL role with
   `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
   NOINHERIT`.
3. Grants runtime CRUD to current tables and sequence access.
4. Configures future-object default privileges for the CI migration
   owner, then creates a new synthetic table.
5. Establishes a **separate password-authenticated database session**
   using the runtime role, and confirms the read-only runtime audit
   passes. It reads restored football records, performs synthetic
   INSERT/UPDATE/DELETE with sequence-generated IDs, and accesses the
   newly created table.
6. Rejects CREATE TABLE, ALTER TABLE, DROP TABLE, TRUNCATE and a GRANT
   attempt under the runtime login using PostgreSQL permission errors.
7. Rolls back synthetic runtime writes and removes the temporary
   future table, grants and cluster-global test role.

The test is strictly guarded to `GITHUB_ACTIONS=true`,
`FOOTSCORE_INTEGRATION_TESTS=1`, local PostgreSQL,
`PGDATABASE=footscore` and the exact
`footscore_restore_ci` target.

**Caveat:** PostgreSQL roles are CLUSTER-GLOBAL, even when granted
permissions in a disposable database. The CI cluster is itself
ephemeral. This script must NEVER be run in a shared or production
cluster; it creates/drops a cluster-global role. No real production
roles or secrets are created by this code.

## Remaining production work

The live Compose configuration still shares a privileged account.
No production access has been changed. An operator must explicitly
provision separate roles, set distinct migration and runtime secrets,
review authenticated service connections, test future migration
grants, monitor and rotate secrets, and separately test new-cluster
role/extension recovery. A disposable integration role test cannot
establish full production least privilege or a security audit pass.

See `RESTORED_DATABASE_SECURITY.md` and
`RESTORED_BACKEND_CHECKS.md` for the previous recovery tests.
