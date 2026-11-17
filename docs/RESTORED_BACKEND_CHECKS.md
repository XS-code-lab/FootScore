# FootScore v0.26 — PostgreSQL catalog and restored FastAPI smoke

Earlier recovery CI already checked a real custom-format backup, AES-256-GCM
encryption, an in-memory S3 upload/download, isolated PostgreSQL restore,
full-row content digests, schema migration and referential integrity.

Version 0.26 adds two independent checks that **row content alone cannot
establish**: PostgreSQL index/constraint/sequence catalog parity and
functional API reads/writes on the restored test database.

## Complete PostgreSQL catalog comparison

The disposable integration CI job runs:

```bash
python tests/compare_restore_catalog.py --target footscore_restore_ci
```

Against the **unchanging synthetic CI source** and the new restored
database, this script reads the public PostgreSQL catalogs without
mutating them:

- Index names and complete PostgreSQL index definitions.
- Table constraint names, types, validation states and definitions.
- Sequence names, parameters and exact `last_value` / `is_called`
  state.

For every category, it checks all objects, including missing or extra
catalog entries. Output contains object counts, mismatched object names
and success status, **never** the actual index SQL, sequence values,
row contents, passwords, user emails or cryptographic hashes.

This is gated to the GitHub Actions disposable integration environment,
with a separate `footscore_restore_...` database. It uses read-only
REPEATABLE READ transactions. This comparison is invalid against an
actively changing production source: new transactions may advance
sequences or modify schema during the backup window.

Unlike data-row hashing, index definition checks can reveal missing
unique/index access structures that affect constraints and query plans.
Comparing sequence state detects a restore that might reuse an ID
or restart a sequence from the wrong position.

Version 0.27 separately checks restored database-local ownership,
effective object/schema ACL privileges, RLS flags and extension versions,
and tests a temporary read-only role's inability to write on the
isolated target (see RESTORED_DATABASE_SECURITY.md).

These checks still do not establish that every index is optimal,
that production query plans are fast, or that production roles,
grants, extensions, collation compatibility, physical storage, WAL
and database-level settings have been restored.

## Restored FastAPI smoke and sequence-backed writes

After **all read-only source/target comparisons are complete**, CI
sets `DATABASE_URL` to the exact local disposable
`footscore_restore_ci` database and runs:

```bash
python tests/restored_api_smoke.py
```

The harness refuses missing GitHub CI flags, a source database URL
or a remote database host before importing the FastAPI app. It uses
FastAPI's in-process TestClient rather than public HTTP or real
external football APIs. It verifies:

- `GET /health` and `GET /ready` on the restored backend.
- `GET /api/v1/matches` includes a known restored synthetic match.
- `GET /api/v1/matches/{id}` returns its original score and status.
- `GET /api/v1/matches/{id}/updates` returns its original durable
  update and cursor replay behavior.
- A new synthetic match and match update can be **written to the
  disposable restored database**, receiving fresh IDs above the
  previously restored highest IDs, and then read via the HTTP API.

The last writes happen **only after** all source-vs-restored comparisons,
so they cannot cause a false mismatch during snapshot checks. The
original synthetic source database is checked again afterward to
confirm the seeded fixture remains readable and unchanged.

No production database, real account data, actual Redis subscriber,
real provider data, SMTP server, FCM device or cloud provider is used.

## Test coverage and boundaries

Unit tests exercise changed index definitions, missing/extra constraints,
sequence-value drift and `is_called` changes, as well as mandatory
isolation checks preventing production/source target use. GitHub CI
uses actual PostgreSQL 16 and Redis 7 services to test catalog parity,
FastAPI reads and sequence-backed writes after real isolated restore.

These remain **small synthetic CI recovery drills**, not a production
sized load test, security assessment or guarantee of uptime, RPO, RTO,
provider historical completeness or API behavior under real traffic.
For a production release, separately validate roles/permissions,
extensions, unique/foreign-key enforcement, read/write performance,
database collation, WAL/PITR, secret restoration, data retention and
independent backup-key recovery.

No automatic recovery, cloud upload, destructive database migration
or production deployment is initiated by this release.
