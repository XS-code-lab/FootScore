# PostgreSQL + Redis integration checks (backend v0.12)

FootScore runs **two separate** GitHub Actions CI jobs:

1. `test`: Ruff, Docker Compose configuration, a synthetic
   provider-to-scouting end-to-end smoke, and the full unit suite.
   These tests use SQLite and fake Redis as appropriate.
2. `integration`: disposable **PostgreSQL 16** and **Redis 7**
   service containers. It upgrades an empty database through **all**
   Alembic revisions, verifies concurrent quota reservations, publishes
   real Redis Pub/Sub match updates, confirms HTTP replay, and tests
   transaction rollback. It then downgrades the disposable database to
   base, upgrades again to head, and repeats those smoke tests.

The CI database password is a non-production fixture; there is no live
API-Football key, player dataset, Firebase secret, or real provider request.

## Running against disposable local services

**Never use a production database** for this process: the migration
roundtrip deletes all data.

```bash
# Start a fresh test-only PostgreSQL and Redis pair.
docker run --rm -d --name footscore-ci-pg \
  -e POSTGRES_USER=footscore -e POSTGRES_PASSWORD=local-ci-only \
  -e POSTGRES_DB=footscore -p 15432:5432 postgres:16-alpine
docker run --rm -d --name footscore-ci-redis \
  -p 16379:6379 redis:7-alpine

pip install -e '.[dev,ml]'
export DATABASE_URL='postgresql+psycopg://footscore:local-ci-only@127.0.0.1:15432/footscore'
export REDIS_URL='redis://127.0.0.1:16379/0'
export FOOTSCORE_INTEGRATION_TESTS=1

alembic upgrade head
pytest -q tests/test_postgres_redis_integration.py

# Only against this empty disposable test database:
alembic downgrade base
alembic upgrade head
pytest -q tests/test_postgres_redis_integration.py
```

To clean up local service containers:

```bash
docker stop footscore-ci-pg footscore-ci-redis
```

Ordinary `pytest -q` skips the real-service tests unless
`FOOTSCORE_INTEGRATION_TESTS=1` is explicitly supplied. CI uses the
flag only in the isolated integration job.

## What the integration test establishes

- PostgreSQL migrations 0001–0010 apply and can reverse on an
  empty disposable database.
- Six concurrent clients reserve against a rolling 60-second
  two-request limit: exactly two reservations succeed and four
  are rejected. Failed attempts do not consume the daily budget.
- **Race condition fixed after the first real PostgreSQL test:** the
  reservation clock is read from PostgreSQL via `clock_timestamp()`
  **after** the provider row lock is acquired. Reading the timestamp
  before waiting for the lock could incorrectly exclude already-committed
  reservations that were 'in the future' relative to a blocked caller.
- Two concurrent PostgreSQL outbox publishers skip each other's claimed
  rows, 16 authentication attempts enforce the 10-attempt per-peer limit,
  and two concurrent password reset consumers cannot use one code twice.
- Two concurrent all-session revoke requests with the same JWT version
  yield exactly one successful revocation (compare-and-swap).
- Two concurrent encrypted SMTP-outbox workers claim separate messages
  under PostgreSQL row locks; no real SMTP connection is made in CI.
- Real Redis receives an update published from a committed
  PostgreSQL outbox row, and the HTTP replay endpoint returns it.
- A deliberately failed ingestion transaction does not persist
  its match update.
- Ordinary `/ready` returns database connectivity status.
  It does not confirm Redis, migration level or data freshness.

## Compose deployment notes

The `api` container now receives the configured API-Football key and
daily/minute limits, so manual commands executed inside that container
share the same accounting database as the scheduler. Keys remain
server-side environment variables; never expose them in public APIs
or commit a real `.env`.

If using the Redis `live` Compose profile, set
`REDIS_URL=redis://redis:6379/0` **for containers** in your
`.env` file. If running Python directly on the host with exposed
Redis port, use a host URL instead; container `localhost` is **not**
the Redis service. The live-publisher depends on a healthy Redis
container. Database migrations remain an **explicit separate step**.

## Not established

- Real API-Football authentication, rate-limit resets and paid/free
  entitlement behavior
- Real user FCM delivery, provider VAR corrections and ML data quality
- Real mobile/WebSocket load and reliability under network partitions
- Production backups, secrets management, security audit, TLS,
  account lifecycle and live-schema migrations with existing data
- Multi-process Redis outbox ordering, retry backoff and retention

A green integration job is evidence of **test service integration**,
not production-readiness. The PR remains Draft.
