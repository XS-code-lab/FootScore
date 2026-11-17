# FootScore v0.25 — full-row synthetic backup content integrity comparison

Prior releases proved an encrypted archive could be downloaded, authenticated,
restored to a *new disposable* PostgreSQL database, and its schema checked.
However, **matching schema and row counts alone cannot detect a changed
football score, missing event payload, altered password hash, or corrupted
email outbox record**.

This release adds a **CI-only, read-only full-row comparison** between the
original, *quiescent synthetic* PostgreSQL test database and its isolated
restored copy.

## What CI checks

The integration job deliberately seeds a larger **synthetic-only** snapshot:
- 181 match records and 1,441 live update records.
- 31 synthetic account records.
- 31 HMAC-style test token records and 31 encrypted-payload placeholders.
- Earlier isolated integration fixtures and all other application's
  database tables are also part of the comparison.

After creating a real PostgreSQL 16 custom archive, AES-256-GCM encrypting it,
simulating S3 upload/download, and restoring to `footscore_restore_ci`,
CI runs:

```bash
python tests/compare_restore_content.py --target footscore_restore_ci
```

**This program intentionally refuses to run outside the disposable GitHub
Actions integration environment.** It requires
`FOOTSCORE_INTEGRATION_TESTS=1`, `GITHUB_ACTIONS=true` and a separate
`footscore_restore_...` target name. It uses only read-only,
REPEATABLE READ PostgreSQL transactions. There is no update/delete
operation, no cloud communication and no change to the source.

For every public base table, including empty and newly added tables,
it discovers the table's PostgreSQL primary key, orders all rows by that
key, renders the **entire row** using PostgreSQL's JSON representation
under UTC time settings and streams batches of 256 rows.

The verifier computes a locally scoped SHA-256 digest per table with
explicit row-byte length framing. It compares both the row count and
digest. A changed payload is rejected even if the number of records
did not change. Missing/extra tables and tables lacking a usable
primary key **fail closed**.

**Privacy:** Only table names, aggregate counts, success/failure and
comparison coverage appear in output. Full user-like rows, raw hashes,
token values, personal email fields, JSON event payloads and credentials
are never written to files, artifacts, logs or webhook messages.
Intermediate hashes exist only in the CI process memory.

## What this proves — and does NOT prove

On a **static synthetic CI database**, matching complete row
representations after the archive roundtrip is stronger evidence than
checking a few known fixtures. The comparison uses PostgreSQL's
canonical *server-rendered row text* in the same server version and
session timezone. It does not compare low-level PostgreSQL file
layouts, indexes, sequence state, server roles, grants, extensions,
large objects, configuration or external services.

**This is NOT a point-in-time comparison suitable for a changing
production database.** PostgreSQL `pg_dump` creates its own consistent
snapshot; an independently read source database can change between
the dump and comparison. A live source comparison would therefore
be misleading without an explicitly captured source snapshot, consistent
manifest, or alternative WAL/snapshot-aware verification method.

This CI test also cannot establish a production recovery-point
objective, recovery-time objective, 100% provider data coverage, backup
key escrow, a cloud IAM policy, or whether a restored application
actually serves real football API traffic.

The test dataset is larger than the original one-row recovery fixture
but remains **tiny relative to production**. No performance claim about
million-record restore times should be inferred.

## Safety and future work

- Never enable this CI-only full-row comparison against live production
  records without a separately reviewed snapshot-consistency design.
- Actual personal or token-like data must not be included in CI fixtures.
- Keep production backups encrypted on secure scratch storage and use
  a separate approved, isolated restore environment.
- For future production drills, build a signed snapshot-time manifest
  and verify indices, sequence state, retention constraints, restore keys
  and application-level smoke tests.
- Continue checking automated production backups through operational
  monitoring; this release creates no new schedules, cloud resources,
  credentials or destructive actions.

See `ISOLATED_RESTORE_AUDIT.md` and `RECOVERY_MEASUREMENTS.md` for
the separate encrypted restore command and phase timing evidence.
