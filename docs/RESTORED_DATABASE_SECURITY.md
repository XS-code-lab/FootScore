# FootScore v0.27 — restored PostgreSQL permissions and security checks

Backend v0.28 adds a read-only runtime-role privilege policy CLI and an
actual separate LOGIN role with DML but without owner/schema powers in
the disposable restored database. See POSTGRES_RUNTIME_ROLES.md.

The preceding isolated recovery drill validated a real synthetic pg_dump,
AES-256-GCM authentication, mock S3 round-trip, all table row contents,
49 indexes, 81 constraints, 21 sequences and FastAPI reads/writes.

Version 0.27 adds checks for **database-local security settings**, which
data rows and index definitions do not establish. All operations are
restricted to a disposable GitHub Actions PostgreSQL 16 service.

## Read-only source vs. restored security metadata comparison

The integration job first runs:

```bash
python tests/compare_restore_security.py --target footscore_restore_ci
```

Only when `GITHUB_ACTIONS=true`, `FOOTSCORE_INTEGRATION_TESTS=1`,
`PGHOST=127.0.0.1`, `PGDATABASE=footscore` and the target name is
**exactly** `footscore_restore_ci` does the command proceed.

It uses separate PostgreSQL `REPEATABLE READ READ ONLY` sessions to
compare the quiescent synthetic source with the isolated restored target:

- **Every public table, view, materialized view and sequence**:
  PostgreSQL role name of the owner, object type, table
  row-level-security status, `FORCE ROW LEVEL SECURITY` status,
  relation persistence, and effective privileges.
- **Object grants**: actual grantee, grantor, privilege type and
  grantability from PostgreSQL's `aclexplode` and `acldefault`.
  A newly public writable table, missing SELECT grant or different
  owner must fail.
- **The public schema**: owner and effective schema privileges,
  including CREATE/USAGE.
- **Installed PostgreSQL extensions**: name, schema and version.
  This checks DB-local extension presence but not installation of
  the extension's operating-system shared libraries in another cluster.

Output intentionally contains only counts and names of mismatched
objects. It never emits SQL definitions, actual role grants, user
records, encrypted payloads, credentials or role secrets.

**Important limitation:** `pg_dump` does not recreate cluster-global
roles or credentials. This CI check uses **the same temporary
PostgreSQL cluster** for source and restored databases, so the
necessary role identities already exist. It cannot prove that
global roles, database-wide grants, passwords, external auth policies,
trusted extensions or user management are recoverable on a clean
production cluster.

## Actual least-privilege negative tests on restored DB

After ALL read-only content/catalog/security comparisons and after
FastAPI's post-restore write test, CI runs:

```bash
DATABASE_URL='postgresql+psycopg://...@127.0.0.1:5432/footscore_restore_ci' \
  python tests/restored_readonly_role_smoke.py
```

The script enforces the exact disposable CI URL and credentials BEFORE
opening a database connection. It then creates a temporary
`NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT` role, grants
only `USAGE` on `public` and `SELECT` on restored tables, and
exercises SQL under `SET LOCAL ROLE`.

The role MUST successfully read the restored match and live-update
tables but MUST receive PostgreSQL `InsufficientPrivilege` for:

- INSERT into a match table.
- UPDATE existing match rows.
- DELETE existing match rows.
- CREATE TABLE in public schema.

Savepoints prevent failed commands from breaking the test transaction;
all test changes remain in the isolated restored database. The role,
its grants and privileges are removed at the end of the test. No role
or grant changes are made to a production server or source database.

A passing negative test proves this **specific temporary role** cannot
perform those operations under this synthetic CI configuration.
It does not prove the app's current operational DB account is
least-privileged. The disposable CI owner is deliberately more
privileged than a production runtime account should be.

## Remaining production gates

- Provision distinct migration/backup administration and application
  runtime roles in a controlled environment. The runtime role should
  not be a superuser and should lack CREATEDB, CREATEROLE and object
  ownership where practical.
- Test real provisioning/role restoration on a separate brand-new
  cluster, including role memberships, default grants, database
  privileges, row-level security policies and extensions.
- Validate network/TLS/host-based authentication, secret rotation,
  OS extension packages, encrypted WAL/PITR, cloud KMS and RTO/RPO.
- Require an independent threat-model and real-data recovery run
  before declaring production readiness.

No actual live AWS S3, football provider, SMTP server, FCM account,
production PostgreSQL cluster or user data is accessed by this PR.
Nothing runs automatically outside the explicit CI test job.
