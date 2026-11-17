# FootScore pilot end-to-end smoke test (backend v0.11)

## Scope

This CI test uses a synthetic `httpx.MockTransport` provider response; it
**never contacts API-Football**, Redis, Firebase, or external services. The
dataset does not represent real football matches or verified historical
data. It validates the integration boundaries, not the provider's real
historical coverage or production performance.

Run locally after `pip install -e '.[dev,ml]'`:

```bash
pytest -q tests/test_pilot_e2e.py
```

GitHub Actions runs this smoke separately **before** the full test suite
so a regression in a cross-service path is obvious.

## Exercised pipeline

1. Reserve one API request and retrieve provider-declared league/season
   capability flags (these are not proof of complete data coverage).
2. Reserve and fetch a mock league fixture, normalize and persist its
   competition, teams, match status, score and durable match update.
3. Reserve and import per-fixture player minutes, goal and provider rating;
   save a receipt that documents the response, not a complete lineup.
4. Reserve and retrieve a player's verified-date-of-birth profile.
5. Create a favorite player then ingest goal event and team statistics.
   Confirm an in-app goal notification and public match update.
6. Advance match status to FT on a separate reserved fixture sync.
   Confirm a final-rating notification.
7. Verify public REST match, player, U22 emerging discovery and replay
   responses match the persisted data. Unranked scouting cohorts remain
   unranked when peer data is insufficient.
8. Simulate the provider correcting a goal to disallowed; verify the
   alert is marked invalid and an update is recorded.
9. Hit the configured sliding minute limit; verify no ninth upstream
   request is issued.
10. Publish retained match updates through a fake Redis object and
    check the update-outbox delivery flags.

Separate tests verify that API-Football 429 responses carry a numeric
Retry-After hint (where provided), are not silently retried, and do not
refund already-reserved quota. Malformed provider JSON/response structures
are rejected instead of generating partial fabricated records.

## Operational probes

`GET /health` and `GET /api/v1/health` are process liveness probes.
`GET /ready` runs `SELECT 1` against the configured database. It returns
503 if the database is unavailable, but **does not verify Alembic revision,
Redis connectivity, scheduler state, API-Football access or complete data**.
Do not use a successful readiness response as proof of production readiness.

Before connecting a real provider:

- Set a new private `DATABASE_URL`, `JWT_SECRET`, `API_FOOTBALL_KEY`
  and, for WebSocket streaming, `REDIS_URL`; never commit credentials.
- Apply `alembic upgrade head` (currently revision 0007).
- Limit initial probing to one league and a few fixtures using
  `app.pilot_cli`, `app.cli` and dry-run `app.historical_cli`.
- Monitor real provider daily/minute remaining quota headers independently;
  the local rate limiter does not know about outside apps sharing the key.
- Do not assume the free plan provides unrestricted historical seasons or
  player statistics. Record discrepancies, 429s and empty responses.
- Test real PostgreSQL and Redis (including transient failures), multi-worker
  races and real production credentials separately before public rollout.

## Known limitations

There is no genuine external integration run here. No live client or phone
push is tested; no actual real-data predictive model is validated. A
provider's corrected time, scorer, team or event ordering can still change
event identity; these are not all resolved by the first reconciliation.
Status code 429 is *not retried automatically*: an operator must wait and
ensure a new request budget reservation. There is no bounded retry policy
for transient transport or 5xx errors yet. One API key used outside the
FootScore database is outside local accounting.
