# FootScore v0.23 — independent, isolated encrypted-backup restore audit

The S3 inventory reports only **object visibility**, not proof that a
database can be restored. This release adds a separate, explicitly opted-in
recovery drill: download a selected encrypted FootScore archive, verify its
AES-256-GCM authentication, decrypt it into private temporary storage,
restore only into a **brand-new disposable** PostgreSQL database, then
independently check the restored schema and basic table consistency.

**Nothing runs automatically on the backend or CI scheduler.**
No production S3, PostgreSQL or user data has been accessed for this
feature. Do not enable it against an unreviewed environment.

## Safe default (no cloud, key or database access)

Obtain a generated encrypted object key from the **read-only** inventory:

```bash
python -m app.backup_inventory --bucket YOUR_PRIVATE_BUCKET
```

The following command **does not connect to S3/PostgreSQL**, download
anything, create a database or require encryption credentials:

```bash
python -m app.restore_audit restore \
  --bucket YOUR_PRIVATE_BUCKET \
  --key 'footscore/2026/11/18/20261118T120000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.fsbak' \
  --target footscore_restore_test_001
```

Replace the example object key with a real key from the inventory.
The required name must match FootScore's timestamped archive format;
path traversal, mismatched dates and arbitrary filenames are rejected.

## Explicit isolated restore

Use a **dedicated disposable PostgreSQL 16 test instance**, separate
from production network access if possible. PostgreSQL credentials
must have permission to create a NEW database. Configure in a trusted
operator environment or secret manager:

- `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` referring to the
  source/reference database for safety checks. Never set
  `PGDATABASE=footscore_restore_...`.
- Protected PostgreSQL password via `PGPASSFILE` or environment
  secret injection (not CLI args).
- A separate, escrowed 32-byte `FOOTSCORE_BACKUP_KEY` in hex,
  matching the archived AES-GCM encryption key.
- AWS SDK credentials with **GetObject** permission restricted to
  the encrypted archive prefix; no remote deletion permissions.
- FootScore's source checkout and compatible PostgreSQL client programs
  `createdb`, `pg_restore`, as used by
  `scripts/postgres_restore_drill.sh`.

After reviewing bucket/key/target names and isolating the test instance,
explicitly execute:

```bash
python -m app.restore_audit restore \
  --bucket YOUR_PRIVATE_BUCKET \
  --key 'footscore/YYYY/MM/DD/TIMESTAMP-UUID.fsbak' \
  --target footscore_restore_test_001 \
  --execute
```

The tool checks all parameters and secrets before contacting S3.
The remote ciphertext is downloaded to a mode-0600 private temporary
file and authenticated locally. An invalid/wrong-key archive cannot
reach `pg_restore`. Decrypted temporary contents are removed on
completion, although deletion is **not secure erasure**; use encrypted
scratch storage and protect snapshots and swap.

The existing restore helper creates a database only when the chosen
`footscore_restore_...` name does not exist and never drops/cleans
an existing target. A matching existing target **fails**, and the tool
never drops production. If restore errors after database creation,
the isolated target is **left in place** for operator investigation;
an operator must review and explicitly delete the disposable test DB.

After a successful restore, the tool opens the **restored target**, not
the source database. It confirms that the Alembic revision matches
the current codebase, reads count-only summaries for critical tables
(matches, users, live updates, one-time tokens, email deliveries,
backup alert states) and checks for orphaned match and security-email
outbox links. It does not output private user rows or tokens.

If the archived schema is older than current Alembic head, the audit
reports a failure even if SQL recovery succeeded; this is a deliberate
**current-code compatibility gate**, not proof the older backup is
unrecoverable with an appropriately versioned application.

## Independently audit an existing restored target

After separately restoring an archive with compatible PostgreSQL tools,
run read-only checks against that **isolated target**:

```bash
python -m app.restore_audit verify \
  --target footscore_restore_test_001
```

This command does not download objects, decrypt files, modify records,
run migrations, create databases or delete anything. The target must
have the safe disposable name and cannot equal `PGDATABASE`.

## Limits and interpretation

Starting in v0.24 the restore command also measures four stages
(download/authenticate, decrypt, PostgreSQL restore and schema audit),
with an optional `--max-drill-seconds` operator budget. The read-only
audit checks unvalidated PostgreSQL foreign-key constraints. These
numbers are **not** a production RTO/RPO; see
[recovery measurements](RECOVERY_MEASUREMENTS.md).

The report deliberately marks:
- `cryptographic_integrity_verified`: true only when the recovery
  command actually authenticated the downloaded encrypted archive.
- `schema_matches_current_head`: true after verification against the
  existing application Alembic head.
- `application_functionality_verified`: false; no server-side user
  journey or real external integration has been exercised.
- `source_completeness_verified`: false; critical table counts do
  not prove all expected rows or external provider state exist.
- `rpo_rto_slo_verified`: false; measured duration is not a
  production-scale recovery-time guarantee.

No automatic backup selection, blind restores, test DB dropping,
secrets provisioning, production WAL/PITR recovery, IAM deployment,
cloud-retention deletion, risk-free failover or actual live restore
certification is provided. A separate operator-reviewed recovery
playbook and periodic independent production-sized drills remain
necessary before a public production launch.

## Test evidence

Mock-S3 unit tests cover no-network dry run, invalid restore targets,
malicious object keys, wrong AES-GCM keys, tampering, temporary file
cleanup and verifier execution. The GitHub disposable PostgreSQL 16
job creates a real synthetic archive, encrypts it, uses mock S3
upload/download, tests recovery CLI orchestration, restores it with
actual PostgreSQL tools into an isolated database, and separately
runs `app.restore_audit verify` plus the existing synthetic
object-by-object comparison checks.

None of those tests access real AWS, a real football provider, real
SMTP or user data.
