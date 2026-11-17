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
Implemented: fixture adapter, normalized fixture model, idempotent fixture writes,
basic relational models, API reads, development import CLI, local per-day quota accounting.
Not implemented: multi-process-safe quota reservations, retries, 429 backoff, provider-side
pagination, event and statistics collection, player identity matching, migration-based schema
management, recurring tasks, Redis, live websockets, scouting or notifications.
