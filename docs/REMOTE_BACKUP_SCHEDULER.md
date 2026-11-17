# FootScore v0.20 — opt-in encrypted PostgreSQL backups to S3

This feature **does not create a bucket, deploy AWS resources, schedule a
cloud job or spend cloud money automatically**. It adds a Python command
that can optionally generate a PostgreSQL backup, encrypt it using
FootScore's AES-256-GCM archive format, and upload **only ciphertext**
to an operator-selected AWS S3 or sufficiently compatible object store.

## Requirements and safe first run

Run on a **dedicated trusted host**, with the FootScore source checkout,
Python 3.11+, PostgreSQL client `pg_dump` and `pg_restore` compatible
with your database, and the optional S3 SDK:

```bash
pip install -e '.[backup]'
```

Supply these securely at the process level, not via command-line
arguments, `.env` committed to Git or publicly printed logs:

- `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` and a protected
  PostgreSQL credential (use `PGPASSFILE` or secrets manager).
- `FOOTSCORE_BACKUP_KEY`: dedicated **64-hex-character** AES-256 key,
  not the JWT or email queue key. Keep escrow and rotation procedures.
- AWS SDK credentials supplied via a restricted IAM role, workload
  identity or other approved boto3 credential chain; no static AWS
  access keys are required by FootScore itself.
- An existing private S3 bucket and an appropriate least-privilege
  policy granting the required conditional PutObject and HeadObject
  operations. Your storage backend must honor `If-None-Match: *`
  atomically; if it does not, the upload **must fail**, not silently
  overwrite an object.

Preview without a database, key, boto3 client or network request:

```bash
python -m app.backup_scheduler --bucket footscore-backup-example
```

Explicitly perform a one-time backup/upload:

```bash
python -m app.backup_scheduler \
  --bucket footscore-backup-example \
  --prefix footscore \
  --execute
```

The bucket name above is illustrative only: this does not set up the
bucket or authenticate to AWS.

**Schedule only after a full recovery test**, on one dedicated host:

```bash
python -m app.backup_scheduler \
  --bucket footscore-backup-example \
  --prefix footscore \
  --execute --watch --interval-hours 24
```

The watch loop starts one backup immediately and then pauses for the
chosen interval (allowed: 6–168 hours). It has bounded command timeout
(15 minutes) but no distributed leader election or exactly-once
scheduler. Use **one** trusted worker only. The watch process must be
supervised and restarted by your own service manager; the API does not
start it automatically. No remote retention deletion is ever attempted.

## What is uploaded and validated

Each run uses a private temporary directory with restricted mode.
The PostgreSQL custom-format `pg_dump` archive is generated locally,
then encrypted by `app.backup_crypto` with authenticated AES-256-GCM
and verified. The local plaintext temp file is removed **before** any
network upload. Only encrypted `.fsbak` bytes are sent.

Object names include a UTC timestamp and random UUID. Uploads use
S3's conditional `PutObject(IfNoneMatch='*')`, a SHA-256 checksum, and
custom metadata. A subsequent `HeadObject` compares the returned
length and digest metadata with the local encrypted file.

- **Single-object limit: 5 GiB.** Larger database archives are rejected
  before upload; a safe multipart design is not part of this release.
- This is a **create-only** feature; it does not list/delete remote
  objects or enforce retention.
- The upload confirmation check is **not independent end-to-end
  verification**: both HTTP response and HEAD come from the same
  storage service. Validate recovery with download + AES-GCM verify
  and an isolated PostgreSQL restore.
- A remote download verifier is available in
  `app.backup_remote.fetch_and_verify_encrypted_archive` for trusted
  Python operators. It downloads to a new private location, refuses
  overwriting an existing path, and verifies AES-GCM before exposing
  the resulting encrypted file. This API never decrypts into a public
  directory.
- Incomplete upload failure never reports success, never deletes a
  remote object, and leaves local plaintext removed when the temporary
  directory exits. Note that ordinary filesystem deletion is **not
  secure erasure**; use encrypted scratch storage.
- If a job succeeds in uploading but fails before recording its
  success, the next run may create another distinct encrypted backup;
  that is safer than overwriting existing objects but can increase
  storage cost. Reconcile remote inventory and costs separately.

## IAM, security and backup strategy

The safest design uses workload identity with permission to PUT only
to a dedicated private bucket/prefix plus HEAD for checking, not broad
bucket deletion. Cloud infrastructure must also enforce private
access, TLS, encryption at rest, versioning/object lock according to
your threat model, monitoring and retention. Bucket access policies,
KMS key permissions and billing are the operator's responsibility.

The FootScore AES-GCM encryption key must be independently recoverable
from the database, cloud account and host. Restoring a working
application may also require `JWT_SECRET` and `EMAIL_QUEUE_KEY`.
Do not co-locate plaintext dump snapshots and the encryption key in
the same unprotected backup destination.

## CI evidence and limits

Unit tests use an **in-memory fake S3 client** to ensure the SDK is
only given authenticated ciphertext, a conditional no-overwrite write,
a SHA-256 checksum, verification metadata, and safe download/restore
behavior. They also exercise dry-run, failure cleanup and required
secret guards. No AWS or S3-compatible account is used.

The PostgreSQL 16 integration CI job creates a *real synthetic*
PostgreSQL archive, AES-GCM-encrypts it, runs the mock upload/download
adapter, validates the downloaded ciphertext, decrypts it, then restores
and verifies data in a **separate disposable PostgreSQL database**.

The code has **not** been verified against an actual S3 bucket, real
IAM role, production database or S3-compatible alternative. The tests
do not establish RPO/RTO, restore reliability at production scale,
backup freshness alerts, a cloud cost ceiling, HA failover or security
audit readiness. Pilot those independently before enabling the worker.
