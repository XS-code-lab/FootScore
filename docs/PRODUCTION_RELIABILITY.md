# FootScore backend v0.13 — outbox and auth abuse guardrails

## Operator observability and outage recovery (v0.17)
Local-only `python -m app.ops_cli` prints queue backlog ages, failed
security-email counts and local API-Football budgets. The optional
`--strict` flag exits nonzero on lag alerts, but this is **not** a
public health or metrics API. Redis publisher retries failed transports
with bounded 1–30-second backoff and finite socket timeouts; failed SQL
acks roll back so Redis reconnect can replay rows. See
[OPS_MONITORING.md](OPS_MONITORING.md).

## Multiple Redis outbox publisher workers
The `drain_outbox` query selects unpublished `live_updates`
under PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`. It locks selected
rows until the worker publishes them and the database transaction commits.
Another worker skips claimed rows and can process other updates instead.
The normal CLI remains `python -m app.outbox_cli --watch`.

- The database outbox is the durable change history; Redis Pub/Sub is not.
  Read `GET /api/v1/matches/{id}/updates?after_id=...` after reconnect.
- **At-least-once**, not exactly-once. A worker crash after publishing
  to Redis but before PostgreSQL commit will cause a replayed publish.
  Clients **must deduplicate on `id`** and handle out-of-order updates:
  `SKIP LOCKED` may let later IDs publish ahead of older locked IDs.
  If any gap is detected, reconcile with the replay API.
- The worker currently performs Redis network calls while holding DB
  row locks. Keep `limit` small and network timeouts bounded. Do not
  promise global strict ordering, delivery to offline subscribers, or
  exactly-once semantics.
- Non-PostgreSQL test databases retain the original select behavior.
  SQLite is not a multiple-server production deployment target.
- No new outbox database columns were needed; existing published_at
  remains the durable ack. Transient Redis faults roll back the ack,
  allowing subsequent retry after connectivity recovers.

## Authentication limits
A new `auth_rate_buckets` table (Alembic revision `0008_auth_rate_bucket`)
charges one attempt per login or valid registration request **before**
password hashing / account lookup. Each bucket uses a fixed 15-minute
time window. Default per-network-peer limits:

| Endpoint | Budget per peer per 15 minutes |
| --- | ---: |
| `POST /api/v1/auth/login` | 10 |
| `POST /api/v1/auth/register` | 5 |

On exhaustion, the server returns HTTP **429** with a `Retry-After`
response header. All attempts are charged, even invalid credentials.
The response does not disclose whether an email is registered.
The bucket ID is an HMAC-SHA256 of the observed peer network address,
the endpoint action and the time window, keyed with the configured
`JWT_SECRET`; it does **not** store a raw address or submitted password.

PostgreSQL serializes concurrent requests using a row lock on the
corresponding key. There is no Redis dependency for authentication.

**Limits and risks:**
- Reverse proxies, shared NATs, schools or mobile carriers can share a
  public client address. Proxy deployment needs correctly configured
  trusted-host/proxy forwarding; never trust arbitrary X-Forwarded-For
  values sent by clients.
- These are *per-peer* limits, not a distributed per-account restriction.
  Rotating source addresses can evade them. Add WAF/IP rules, per-account
  risk detection and monitoring before Internet-scale operation.
- Fixed windows permit bursts around boundaries. Add an atomic rolling
  window when a stricter global guarantee is required.
- Strong `JWT_SECRET` (32+ chars) must be set for auth. Rotating
  the secret invalidates existing access tokens and changes HMAC buckets.
- Stale bucket rows accumulate until `python -m app.maintenance_cli --execute`
  is called by a trusted periodic job (dry run by default).
- Backend v0.14 adds one-time HMAC-hashed email verification/password reset
  codes and revocable JWT session versions. Backend v0.15 adds authenticated
  all-device logout, password change, and optional bounded maintenance.
  See ACCOUNT_RECOVERY.md and SESSION_SECURITY.md.
- No login cooldown, MFA, refresh tokens, durable email send queue,
  IP anomaly signals or email verification have been added.

## CI coverage
The existing SQLite unit suite tests rate limits and rejects spoofed
X-Forwarded-For values. A disposable PostgreSQL 16 + Redis 7 CI job
also tests 16 simultaneous authentication attempts and two competing
outbox publisher workers. These tests do not contact a real football
provider or send real phone push messages.

Run schema migration **before** deploying the updated web process:
`alembic upgrade head`. Never downgrade real production databases for CI.

This remains a development security and delivery baseline, not evidence
of readiness for public Internet traffic.
