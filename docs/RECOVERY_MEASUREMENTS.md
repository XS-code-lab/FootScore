# FootScore v0.24 — measured isolated restore evidence

FootScore can already perform an explicit encrypted S3 download and
recovery into a **new, disposable** PostgreSQL database. Version 0.24
adds timing evidence so operators can compare pilot recovery runs
without mistaking successful unit tests or object visibility for a
production recovery-time guarantee.

## Measured stages

The opt-in `app.restore_audit restore` command now records four
**nonnegative, local-process measured** stage durations in seconds:

1. `download_authenticate` — fetch encrypted S3 object into a private
   file and verify its AES-256-GCM authentication tag.
2. `decrypt` — decrypt into a separate private temporary dump.
3. `postgres_restore` — execute the safe, new-database restore helper.
4. `schema_audit` — independently connect to the restored PostgreSQL
   target and check the current Alembic revision, critical tables,
   unvalidated foreign-key constraints and dangling match/email
   outbox links.

The overall `total_elapsed_seconds` also includes small overheads
between stages; it need not equal their rounded sum. The restore
helper prints its own **whole-second** elapsed time for the
`createdb` + `pg_restore` command to aid CI troubleshooting.

For a zero-network planning run, use:

```bash
python -m app.restore_audit restore \
  --bucket YOUR_PRIVATE_BUCKET \
  --key 'footscore/YYYY/MM/DD/TIMESTAMP-UUID.fsbak' \
  --target footscore_restore_drill_001 \
  --max-drill-seconds 900
```

Only after configuring approved S3 read access, a separately escrowed
`FOOTSCORE_BACKUP_KEY`, isolated PostgreSQL and an encrypted scratch
directory, append `--execute` to perform the drill. No backup
scheduler or recovery worker is automatically enabled.

## Operator-selected duration budget

`--max-drill-seconds` optionally sets a local timing target from
**1 to 3600 seconds**. The JSON result includes:

- `measurements.phase_seconds` and `total_elapsed_seconds`.
- `operator_drill_budget_seconds`: configured target, otherwise null.
- `operator_drill_budget_met`: true or false when configured,
  otherwise null.
- `observed_at`: timezone-aware UTC timestamp.
- `production_rto_verified=false`,
  `production_rpo_verified=false` and
  `complete_source_data_verified=false` unconditionally.

An exceeded local budget is visible as **false**, never converted
into a success claim. It is a measurement for capacity planning,
not an agreed production RTO, SLO or service-level guarantee.
The process deliberately does not delete or replace a restored
database when the timing target is missed.

## Schema evidence

Read-only post-restore audit now returns
`foreign_key_constraints_validated=true` only when PostgreSQL reports
zero unvalidated foreign keys, and checks that no outbox rows point to
missing matches or email tokens. It also reports
`critical_table_count`, safe table row counts and
`audit_elapsed_seconds`. A corrupt or incompatible schema fails the
audit rather than appearing healthy.

Critical table counts **do not establish completeness** relative to
the original snapshot or external provider records. A particular
table may legitimately contain zero rows in a pilot. No individual
user email, session code, football event or private payload is
included in this output.

## Verification and limits

The GitHub Actions integration job creates a **synthetic** PostgreSQL
16 backup, encrypts it, sends it through mock S3, and restores into
a disposable database. It now asserts independent schema audit JSON
(including validated FK constraints, zero dangling outbox links,
six critical tables, and an observed audit duration). It also tests
phase timing reports and duration budgets, and retains the
object-by-object synthetic restore comparison.

Version 0.25 additionally verifies the *full content* of every public
application table against the static, synthetic CI source after isolated
restore. That comparison is limited to a **quiescent CI database**, not
a live production snapshot (see RESTORE_CONTENT_INTEGRITY.md).

No production dataset, real S3 cloud, high-availability failover,
external provider API, actual SMTP mailbox or cloud credential is
used. Large-database restore duration, WAL/PITR catch-up, application
traffic restoration, multi-region behavior, secrets rotation and
full recovery RPO/RTO **remain unverified**. Run regularly reviewed
off-site drills on production-scale replicas to establish those.
