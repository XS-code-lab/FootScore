# FootScore v0.21 — read-only remote backup freshness and retention planning

FootScore's S3-compatible backup scheduler (v0.20) can **create** encrypted
archives. This module adds a separate, operator-invoked **read-only inventory**
that reports how recently an archive-shaped object was listed and what
older objects *could* be considered for retention review.

**No remote deletion is implemented.** This is deliberately a **proposal**,
not a storage lifecycle policy or automatic S3 cleanup. No bucket, schedule
or public alert delivery is deployed in this release.

## Operator commands

Install the optional S3 dependency and configure a restricted read-only
AWS credentials chain / workload identity for an existing private bucket:

```bash
pip install -e '.[backup]'
python -m app.backup_inventory --bucket YOUR_PRIVATE_BUCKET
```

Unlike `app.backup_scheduler` without `--execute`, this command **does
contact S3** using `ListObjectsV2`. It does not contact PostgreSQL or
use `FOOTSCORE_BACKUP_KEY`, because it inspects object listing metadata
only. An operator account needs permission to list the restricted bucket
prefix; it should **not** have `s3:DeleteObject` for this workflow.

For an external job, return a nonzero status when no recent candidate
is listed:

```bash
python -m app.backup_inventory \
  --bucket YOUR_PRIVATE_BUCKET \
  --prefix footscore \
  --max-age-hours 30 \
  --retention-days 30 \
  --keep-latest 3 \
  --strict
```

- Exit **0**: listing succeeded; with `--strict`, a recently created
  matching object is visible. This does **not** mean it is decryptable.
- Exit **2**: listing succeeded, but the strict freshness gate sees no
  recent candidate (`missing` or `stale`).
- Exit **1 / argument error**: cloud/listing/parsing/configuration failed.
  Failures are not converted to "no backups"; retry after investigation.

All output is JSON. The inventory is designed for *trusted operators*;
object keys can reveal operational naming conventions, so avoid logging
them to a public monitoring endpoint.

## What counts as a backup candidate

The tool **only** recognizes a FootScore-generated archive name under
the chosen prefix:

```text
footscore/YYYY/MM/DD/YYYYMMDDTHHMMSSffffffZ-<32 lowercase hex>.fsbak
```

It checks date/path alignment, a plausible nonzero encrypted size,
time-zone-aware S3 LastModified, timestamp ordering, and a storage
class that permits immediate reads. Malformed, future-dated, unrelated,
and unsupported storage-class objects are counted as ignored.

The report's `freshness.state` is one of:

- `recent_unverified`: a matching object claims a timestamp newer than
  the configured threshold.
- `stale`: at least one matching object exists, but none is recent.
- `missing`: no matching candidate is visible.

`cryptographic_integrity_verified` and `restore_verified` are
**always false**. Object names and remote listings can be spoofed or
stale. To prove recoverability, separately download the ciphertext,
verify AES-256-GCM with the correct private key and restore into a fresh
isolated PostgreSQL database (see `ENCRYPTED_BACKUPS.md`).

## Retention proposals — NEVER automatic deletion

Defaults: retain backups for at least **30 days** and protect at least
the **three most recent recognized archives**, regardless of age.
Options restrict retention to 30–3650 days and newest protection to
3–100 objects. A candidate is listed only when **both** its embedded
creation time and S3 LastModified are older than the cutoff and it is
not among the protected newest backups.

`retention_plan.dry_run=true` and `delete_calls=0` are invariant.
The output enumerates candidate object keys, approximate bytes and
claimed creation time; **no DeleteObject is called**, even with
`--strict`. Never turn this planning output into automatic deletion
without independent restore-verification, legal/operational retention
policies, object-lock/replication review and explicit deployment approval.
The CLI has no `--execute` flag.

## Pagination and failure safety

Listing is bounded to **20 pages and 10,000 objects**, with pagination
tokens checked for absence/repetition and duplicate recognized keys
rejected. An incomplete, malformed or failed listing raises an error
rather than returning a false "healthy" result.

The limits are deliberately conservative for a small pilot. If the
prefix contains more than 10,000 objects, use an appropriately scoped
backup prefix or create a reviewed design for reliable large inventory
pagination. The tool is **read-only**; it does not validate actual
provider IAM access in CI.

## Evidence and limitations

Unit tests use a fake S3 list API to cover normal recent/stale/missing
results, safe retention candidates, safeguards, pagination, duplicate
objects, future timestamps, invalid storage classes, and strict CLI
exit codes. GitHub CI's PostgreSQL integration job generates a real
synthetic encrypted archive, passes it through a mock S3 upload/download
and then confirms the inventory recognizes the recently created file,
still without contacting AWS.

No real S3 listing, production alert delivery, email notification,
remote deletion, legal retention policy, off-site immutable storage,
real restore scheduling, RPO/RTO guarantee or cloud-billing threshold
is established. Backend v0.22 adds an optional, explicit HTTPS notification worker with
PostgreSQL deduplication, recovery events and reminders (see
[backup alerts](BACKUP_ALERTS.md)). CI uses a mocked HTTP transport and
synthetic S3 metadata; no external webhook has actually been delivered.
