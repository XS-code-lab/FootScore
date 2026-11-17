# FootScore v0.16 — encrypted security-email delivery outbox

FootScore now persists one-time email-confirmation/password-reset mail jobs in
PostgreSQL before returning the HTTP response. **No mail is sent inside an
API handler.** The worker sends mail only when independently configured and
started. No live SMTP provider or real mailbox is included in CI.

## Operational setup

Apply Alembic migration `0010_email_delivery_outbox`.

Configure **two independent server-only secrets**, not client-facing config:

```bash
# Generate fresh values and store them in a secret manager; never commit them.
python -c "import secrets; print(secrets.token_urlsafe(48))" # JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" # EMAIL_QUEUE_KEY
```

Example variables (placeholder values are NOT valid production credentials):

```dotenv
JWT_SECRET=<private-random-secret>
EMAIL_QUEUE_KEY=<Fernet-key-from-command-above>
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=<SMTP-account>
SMTP_PASSWORD=<SMTP-app-password>
SMTP_SENDER=noreply@example.com
REQUIRE_VERIFIED_EMAIL=false
```

Run a one-off sending pass:

```bash
python -m app.email_cli --limit 10
```

Or explicitly opt in to the Compose worker:

```bash
docker compose --profile email up -d email-worker
```

This worker runs every 30 seconds; no automatic mail job is started with
the API. Only enable `REQUIRE_VERIFIED_EMAIL=true` after actual deliverability
checks, spam/DKIM/SPF/DMARC verification and existing-account migration
planning. If SMTP **or** EMAIL_QUEUE_KEY is missing, the API preserves the
generic reply without creating a code it cannot enqueue. The worker
requires the encryption key and keeps pending encrypted rows untouched
while the SMTP service is unconfigured.

## Data lifecycle / security

- The user requests a confirmation or reset email. Only an eligible
  account gets a one-time code (60 min verification, 15 min reset).
- The code's **HMAC** is stored in `auth_one_time_tokens.token_hash`; its
  **Fernet-encrypted ciphertext** is stored in `email_deliveries.encrypted_code`.
  The plaintext code is never persisted or returned to API callers.
- The token hash and encrypted message are committed **in one database
  transaction**. A crash before commit does not leave a live token
  without its delivery task.
- Issuing another code invalidates earlier pending codes for the same
  purpose. The worker refuses to deliver codes that are consumed,
  expired, belong to inactive users, or are obsolete after confirmation.
- Once SMTP accepts the message the ciphertext is **cleared** from
  the outbox row; after expiry or five unsuccessful attempts it is also
  removed. The token hash remains until ordinary auth retention cleanup.
  If decryption fails (wrong or rotated key), the row becomes `failed`
  and stays encrypted for operator investigation: it is NOT sent.
- The cleanup worker removes encrypted outbox rows referencing an expired
  token **before** deleting the parent token. There is no unencrypted
  code in database audit logs.

## Retry and concurrency

- Uses `FOR UPDATE SKIP LOCKED` on PostgreSQL so multiple workers may
  claim separate queued messages.
- SMTP errors are retried at most **five attempts** with delays
  **60, 120, 240, 480 seconds** between attempts (capped at 900 sec).
  Expired reset or confirmation codes are never sent even if retries
  remain. There are no unbounded retries and no SMTP calls when
  credentials are missing.
- Only generic error categories are stored (`smtp_temporary_failure`,
  `smtp_retry_exhausted`, `decrypt_failed`); SMTP server errors and
  potentially sensitive raw messages are not stored or logged.
- SMTP acceptance and the subsequent database commit are not atomic.
  If the process crashes after SMTP accepts the email but before the
  database marks `sent`, a **duplicate email is possible**. This is
  *at-least-once attempted delivery*, NOT exactly-once, guaranteed inbox
  arrival or bounce tracking.
- The worker holds a DB row lock during the bounded SMTP call (10-sec
  timeout). Operate in small batches; database lock duration and queue
  length must be monitored before scaling. Worker timeouts and faults
  cannot be used to infer recipient inbox acceptance.

## Critical key-management requirements

`EMAIL_QUEUE_KEY` is **not** the JWT signing secret and must never be
exposed to browsers, committed into Git, or printed into logs. Rotate it
only after the queue has been drained or after implementing dual-key
decrypt support: replacing this key without migrating pending ciphertext
makes queued codes undecipherable. Database backups containing queued
ciphertext must be encrypted and tightly access-controlled; possession
of both the database and the queue key can disclose pending codes.

## Test evidence and remaining limitations

Unit tests cover ciphertext storage, delayed send, retry/backoff,
replacement of stale codes, missing SMTP, expiry, decrypt failure,
cleanup and password reset. Disposable PostgreSQL integration tests
cover parallel workers (no duplicate claims).

External SMTP acceptance and actual mailbox delivery have **not** been
verified. There is no provider feedback/bounce webhook, durable
SMTP-server acceptance receipt, delivery metrics dashboard, tenant
isolation, full email-enumeration masking at registration, or proper
sender-domain identity validation. Continue to treat this backend as
a development baseline rather than a public production deployment.
