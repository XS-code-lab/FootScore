# FootScore backend v0.14 — email verification and password recovery

This is a backend-only implementation, **not** an email delivery service
account or confirmed live SMTP deployment. No real email credentials are
included and CI never contacts an SMTP server.

## Routes

| Action | Endpoint | Auth |
|---|---|---|
| Ask to resend email verification | `POST /api/v1/auth/verification/request` | Public; per-peer rate limit |
| Confirm email verification | `POST /api/v1/auth/verification/confirm` | One-time emailed code |
| Request password reset | `POST /api/v1/auth/password-reset/request` | Public; per-peer rate limit |
| Confirm new password | `POST /api/v1/auth/password-reset/confirm` | One-time emailed code |
| Check verification status | `GET /api/v1/auth/me` | Bearer JWT |

Example bodies:

```json
{"email": "scout@example.com"}
```

```json
{"token": "the-code-from-your-email"}
```

```json
{"token": "the-code-from-your-email", "new_password": "LongNewPassword123!"}
```

Registration still creates the account with `email_verified_at=NULL`.
When SMTP is configured, it also sends the first confirmation code.

The request endpoints return the SAME generic 202 response for existing
and unknown addresses; neither the code nor the account's existence
is exposed through these endpoints. Login and registration preserve
their existing response behavior. A token confirmation returns 400 for
unknown, expired, used or wrong-purpose codes.

## Secrets and delivery

Configure, via server-only environment variables or a secure secret store:

```dotenv
JWT_SECRET=<strong-random-secret-at-least-32-characters>
REQUIRE_VERIFIED_EMAIL=true
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=<smtp-account>
SMTP_PASSWORD=<smtp-password>
SMTP_SENDER=no-reply@example.com
```

**Configure and validate SMTP before switching on
`REQUIRE_VERIFIED_EMAIL=true`.** The default is false for compatibility
with existing development accounts. With it enabled, unverified users
cannot sign in or continue using a prior JWT. Existing accounts retain
an unverified status after migration and need a confirmation email.

Mail now uses an **encrypted transactional delivery outbox** (v0.16):
the API commits an HMAC token hash and Fernet-encrypted code in one transaction;
a separate SMTP_SSL worker handles bounded delivery attempts and retries.
Set a distinct `EMAIL_QUEUE_KEY`, configure SMTPS and opt in to the
`email-worker` Compose profile. Codes remain encrypted at rest, and expired
or invalidated codes are skipped. SMTP acceptance is *not* a guarantee of
inbox delivery; a crash after SMTP acceptance can cause duplicates.
See [email delivery outbox](EMAIL_DELIVERY_OUTBOX.md) for setup, security,
retry policy and key rotation precautions.

## Token security and sessions

- Token is generated with `secrets.token_urlsafe(32)` (~256 bits).
- **Only HMAC-SHA256(token, JWT_SECRET)** is stored in PostgreSQL/SQLite.
  The raw code exists transiently inside the sending process and email.
- Confirmation expiry is 60 minutes; reset expiry is 15 minutes.
- Issuing a new code consumes prior outstanding codes for the same
  user/purpose. A code can be used only once, enforced by PostgreSQL row
  locking on confirmation. Wrong-purpose tokens are rejected.
- Successful password reset increments `users.session_version` and
  revokes prior JWTs at subsequent API requests (no token blacklist).
  New JWTs include `ver`; the default of 0 keeps older developer JWTs
  working until the first password reset.
- Codes are never returned in API responses or logged. While awaiting delivery,
  a Fernet-encrypted copy exists in the email outbox, not plaintext.
- Request endpoints have separate, HMAC-keyed 15-minute per-peer limits
  (3 verification and 3 reset requests), in addition to login (10) and
  registration (5). Do not trust arbitrary X-Forwarded-For headers.
- JWT sessions already issued remain valid after email confirmation,
  unless `REQUIRE_VERIFIED_EMAIL=true` is enabled, which blocks
  unverified sessions at request authentication.

## Expired data cleanup

This maintenance command is **dry run by default**:

```bash
python -m app.maintenance_cli --limit 1000
```

To delete at most 1,000 expired authentication bucket/token rows:

```bash
python -m app.maintenance_cli --execute --limit 1000
```

The command is idempotent on expired state, and does **not** delete fresh
one-time tokens or active rate-limit buckets. Add a trusted cron or a
single scheduled worker to execute it periodically; no background
scheduler is automatically activated by this release.

## Tests and limits

`tests/test_account_recovery.py` verifies token hashing, expiry,
one-time consumption, verified-login gate, password reset and old JWT
revocation, absent SMTP behavior, generic responses and cleanup.
Disposable PostgreSQL CI also tests concurrent consumption of one code.

Signed-in users may also change their password or revoke all JWTs using
`/api/v1/auth/password/change` and `/api/v1/auth/sessions/revoke-all`.
These require the current password and perform a compare-and-swap database
session version update (v0.15); see [session security](SESSION_SECURITY.md).

This is a solid starting point, **not production identity assurance**:
registration still reveals duplicate email addresses (409), SMTP is
synchronous and lacks a durable delivery queue, timing may vary between
known/unknown accounts, there is no MFA or user-requested account deletion,
reverse-proxy IP correctness needs deployment validation, and compromised
email addresses remain account takeover risks.
