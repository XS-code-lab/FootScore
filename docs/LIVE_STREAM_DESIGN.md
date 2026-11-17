# FootScore live streaming and provider event correction (v0.6)

## Architecture
- Ingestion transactions insert public `live_updates` rows for meaningful score,
  event, team-stat and player-stat changes. These rows survive worker outages.
- A **single** trusted outbox worker publishes to Redis Pub/Sub after DB commit.
  Start: `python -m app.outbox_cli --watch`.
- WebSocket: `/api/v1/live/matches/{match_id}`. Requires REDIS_URL.
  It sends a match snapshot and subsequent Redis updates. Clients should key
  messages by monotonically increasing `id`; duplicates are possible.
- Replay: `GET /api/v1/matches/{match_id}/updates?after_id=<id>&limit=100`.
  Cursor replays match events from the DB independent of Redis retention.
- Public match data only. User-specific notifications are NOT published to
  Redis match channels.

## Event corrections
- Provider event identity hashes time, team, player and type with duplicate
  occurrence index; it intentionally excludes mutable detail and assister.
- `upsert_match_events(..., reconcile=True)` requires an authoritative
  **complete** snapshot and retracts rows absent from nonempty snapshots.
- Empty snapshots are treated as untrusted/transient unless `allow_empty=True`
  is explicitly requested after independent verification.
- Retracted rows are retained (`is_active=False, retracted_at`), excluded from
  default read APIs and announced via `event_retracted` updates.
- Existing goal/assist inbox notifications linked to such events are marked
  `invalidated_at`; pending FCM deliveries are canceled. Already delivered
  device pushes cannot be unsent. A corrected active event may restore inbox
  status, but does not retry already sent pushes.

## Start with Docker Compose
Set `REDIS_URL=redis://redis:6379/0` in the API environment (.env for
Compose), then:
```bash
docker compose up -d db
docker compose run --rm api alembic upgrade head
docker compose --profile live up -d api redis live-publisher
```
Match update streams do not themselves poll the external football API.
Importing fixtures and their details consumes API quota; use controlled
`python -m app.live_cli --match-id <id>` as needed. Without API keys or data,
only manually ingested test data will appear.

## Caveats before production
- Redis Pub/Sub is ephemeral; resume from the persisted REST replay API.
- No robust replay retention policy, reconnect backpressure or per-client
  quotas; slow clients and many idle connections can consume resources.
- The publisher is **at least once** and single-worker; crash after publish
  but before commit may deliver duplicates. Clients must deduplicate by id.
- Some providers correct event time, team or scorer, causing a replacement
  identity. Those changes are recorded as retraction + new event; further
  identity matching and notification compensation are necessary.
- Existing rows created before the revised identity algorithm may need
  a one-time remapping/backfill before deploying this migration on real data.
- VAR/provisional goals may send an early notification which is later
  invalidated. Do not claim perfectly accurate pre-VAR goal alerts.
- No user-authenticated private WebSocket channels implemented yet.
- Configure rate-limits, TLS, health checks and worker locking before launch.
