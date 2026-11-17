# Free API strategy (November 2026)

## Initial provider
API-Football free tier: **100 requests/day**, **10 requests/minute**.
Check each competition/season's actual fixture, events, player statistics and lineups coverage before advertising the feature.

Other options:
- football-data.org free tier: 10 requests/minute, restricted competition coverage.
- TheSportsDB free tier: 30 requests/minute, limits and available data vary by endpoint.

Official references:
- https://www.api-football.com/pricing/
- https://www.api-football.com/news/post/how-to-optimize-api-sports-calls-and-quota-usage
- https://docs.football-data.org/general/v4/policies.html
- https://www.thesportsdb.com/API.php

## First-stage budget policy
- Reserve at least 10 requests/day for development/diagnostics.
- Use scheduled low-frequency sync for a small pilot league set, not every league.
- Do not poll all games every few seconds using a free plan.
- Persist timestamps; flag stale information and unavailable statistics.
- Normalize and deduplicate provider fixtures and events before notifying clients.
- Never fabricate xG, player ratings, cards or corners when provider lacks them.
- Use server-only API keys; never place secrets in Git.
- Review provider redistribution, caching and attribution rules before a public launch.

## Implementation status
Implemented: fixture adapter, idempotent fixture/event/player-stat ingestion,
PostgreSQL/SQLite daily quota and rolling-60-second request reservation,
versioned Alembic migrations,
small pilot recurring sync, Redis/WebSocket match streams, U22 scouting,
notification inbox and optional FCM outbox. Historical fixture-player backfill
now has a quota-aware dry-run planner and local fetch receipt audit.
League-season provider feature probes can be run explicitly after configuring
a server-side API key; coverage flags are NOT proof of full season data.

Not implemented / not production-verified: cross-application per-minute coordination,
429 exponential retry, provider account-wide usage reconciliation, full fixture
and player-stat season completeness audit, cross-provider identity matching,
complete VAR correctness, multi-worker outbox locking, live API load testing,
real-device push validation or trained real-world ML outcome forecasting.
Free-plan historical-season availability must be verified for each request.
