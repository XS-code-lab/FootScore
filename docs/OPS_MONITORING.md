# FootScore v0.17 — operator monitoring and Redis outage recovery

This release is a **local operations baseline**. It does not install a
hosted monitoring service, expose a public metrics endpoint, or promise
production uptime. All readings come from the FootScore SQL database.
No Redis/SMTP network connection or football API call occurs during a
normal operations snapshot.

## Read-only operations snapshot

After `alembic upgrade head` (new revision
`0011_match_outbox_index`), a trusted operator can run:

```bash
python -m app.ops_cli
python -m app.ops_cli --strict --max-match-age 120 --max-email-age 300
```

The command returns JSON suitable for restricted ops scripts. It reports:

- Pending match-update count and age of the oldest unpublished update.
- Pending, currently due and failed security-email jobs, plus oldest
  pending age.
- **Local-only** daily API-Football requests reserved, reservations in
  the preceding 60 seconds and local remaining daily/minute allowances.
- An `assessment` status of `ok` or `attention`, with stable
  machine-readable alert codes.

With `--strict`, an `attention` result exits with code **2**. Without
it, an alert is informational and does not fail the command. If there
is no pending mail or no pending match update, the corresponding oldest
age is `null` (not zero and not an invented observation).

The default alert thresholds are oldest unpublished match update older
than **120 seconds**, and a due security email whose oldest pending
message is older than **300 seconds**. Any permanently failed security
email also raises an alert. Tune these for your deployment. If the
Redis or email worker profile is deliberately disabled, the alert can
indicate a known backlog rather than a server incident.

**Not exposed over HTTP.** This CLI intentionally emits no player names,
account email addresses, raw IPs, JWTs, password reset codes, SMTP
configuration, encrypted payloads, match-event content, or external API
keys. Protect its console output as operational metadata and avoid
public logging. The snapshot cannot measure real provider-account quota,
external API usage, Redis connectivity, recipient inbox delivery or
end-user perceived latency.

## Match publisher recovery policy

The optional Redis publisher is still:

```bash
python -m app.outbox_cli --watch --limit 100
```

A failed Redis connection or DB transaction now triggers a bounded
retry schedule of **1, 2, 4, 8, 16, then 30 seconds** (default ceiling).
Success resets the backoff. Connections use a 3-second connect/read
timeout so a blocked socket cannot retain PostgreSQL outbox row locks
indefinitely. One-off invocation (without `--watch`) propagates the
error and exits nonzero. Operational logs include only a generic
failure category and delay, not exception messages that might leak a
Redis connection URL.

Database updates are published only after ingestion has committed.
Their publication acknowledgement is written inside a database
transaction. When Redis becomes unavailable halfway through a batch,
that SQL transaction rolls back, so none of those rows is marked
published. On recovery, the batch can be sent again. **Some clients
may have received messages before the crash**, so the stream offers
*at-least-once*, not exactly-once delivery. Always use update `id`
to deduplicate and GET
`/api/v1/matches/{match_id}/updates?after_id=<last_id>` to fill gaps.
Redis Pub/Sub itself is ephemeral and does not replay missed messages.

On PostgreSQL, `FOR UPDATE SKIP LOCKED` allows multiple publishers to
claim different rows. Later IDs can publish before earlier ones, and
a slow Redis/network worker holds SQL row locks until commit. Clients
must not assume global ordering. The new
`ix_live_updates_published_created` index improves pending queue
scans and oldest-outstanding queries; it has no retention policy or
replay-truncation behavior.

## Test coverage

- Read-only SQLite tests verify counts, backlog ages, private-data
  exclusion, local quota math and operator alert thresholds.
- Offline failure simulation confirms a Redis error after the first
  published message **rolls back all database acks**, followed by
  a successful retry and persistent replay without data loss.
- CI's real PostgreSQL 16 + Redis 7 job repeats the partial-publication
  failure/recovery scenario and verifies it still works after
  Alembic downgrade/upgrade on a **disposable** database.
- Full CI checks Docker Compose and Ruff; it contacts no live
  API-Football or SMTP provider.

## Still to do before public production deployment

Configure a trusted metrics exporter, authenticated monitoring and
on-call alert delivery; verify Redis and SMTP externally; measure
queue-lag distributions and provider upstream behavior; add load tests
and service-level objectives; implement backups and restore drills,
worker health probes and provider-request-log retention. The new CLI
does not send automatic alerts or schedule itself.
