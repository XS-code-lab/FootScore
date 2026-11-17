# FootScore backend v0.15 — session revocation and retention

This release adds backend-only account session controls and optional
bounded cleanup of expired security state. It does **not** add a mobile
or web frontend and has no real SMTP/Firebase delivery configured.

## Authenticated session routes

`POST /api/v1/auth/sessions/revoke-all`

```json
{"current_password":"the-current-account-password"}
```

Requires a valid `Authorization: Bearer <JWT>` and the current password.
On success returns `{"sessions_revoked":true}`.

`POST /api/v1/auth/password/change`

```json
{
  "current_password":"the-current-account-password",
  "new_password":"a-different-long-password"
}
```

Requires a valid JWT, the current password and a different new password of
12–128 characters. Success returns
`{"password_changed":true,"sessions_revoked":true}`.

**Both endpoints revoke ALL JWTs for that user**, including the JWT used to
make the request. A user must sign in again afterward. Because JWTs are
stateless, there is no per-device session list or selective single-device
logout in this release. Already-issued JWTs are checked against the user's
stored `session_version` on every protected request. The API's own
short-lived tokens cannot be used after revocation, but an independently
compromised account may still sign in again if its password was not changed.

Both routes charge a database-coordinated 15-minute per-peer bucket of
five attempts, even on a wrong current password, returning HTTP 429 and
Retry-After when exceeded. Shared NAT peers may share the bucket. This is
not a complete account takeover prevention system.

### Concurrency safeguards

The session version is captured from the authenticated request before
charging the rate-limit bucket, which commits and expires ORM objects.
The subsequent SQL update atomically checks that the version has not
changed and increments it **in the database**. This protects against
concurrent stale-session writes; the loser receives a 401 and must sign
in again. Successful password reset also increments the session version
in the database rather than assigning a potentially stale Python value.

The PostgreSQL CI job tests two simultaneous revoke-all operations with
the same expected version and verifies only one succeeds. The normal
unit suite covers old-JWT invalidation, password verification, disallowing
the same new password, and fresh login after password change.

## Optional retention worker

`auth_rate_buckets` and `auth_one_time_tokens` already support retention
cleanup. This release adds a bounded worker that can be used in unattended
deployments. It is **not started automatically by the API**.

To preview a single cycle without mutating data:

```bash
python -m app.maintenance_worker --batch-size 1000 --max-batches 10
```

To perform a bounded single cleanup:

```bash
python -m app.maintenance_worker --execute
```

To run a single dedicated worker every 24 hours in Docker Compose:

```bash
docker compose --profile maintenance up -d security-maintenance
```

That explicitly opted-in service runs the following command internally:

```bash
python -m app.maintenance_worker --execute --watch --interval-seconds 86400
```

- Each cycle deletes a maximum of ten batches of up to 1,000 expired rows.
- The cleanup is idempotent and never selects unexpired auth state.
- A dry run checks only the first batch and does not spin on unchanged rows.
- Use **one** dedicated maintenance worker. This is not a global
  distributed coordinator for cleanup; extra workers are unnecessary.
- Do not run migrations backwards against a database containing user data.
- Database connectivity failures are logged and retried on the next
  scheduled pass; no fatal recovery guarantee is claimed.
- The worker needs DB credentials only, not JWT or SMTP secrets.

## Production limitations

No live SMTP transaction, provider API data, email-delivery evidence or
real user device sign-out has been tested. There is no refresh-token
rotation, durable SMTP outbox, account deletion, MFA, breach-password
screening, production reverse-proxy security audit or WAF. A leaked JWT
used before the revocation request is handled remains valid until the
version changes. An authenticated action may race with a revocation
already in progress; enforce transaction-specific checks for high-risk
operations as the application expands.

This is an incremental backend hardening step, not public launch approval.
