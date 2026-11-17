# Live match details: implementation notes

## Scope implemented
- API-Football v3 `/fixtures/events?fixture=...` and `/fixtures/statistics?fixture=...`.
- Normalized event classifications: goal, yellow_card, red_card, substitution, var, other.
- Persist raw provider event payload including assister/substitute identities.
- Match-event fingerprint: SHA-256 of stable fields and occurrence index; identical
  response snapshots do not create duplicate rows.
- Persist per-team statistics preserving integer, percentage-string or null values.
- Read-only REST endpoints to retrieve match events and team statistics.
- Manual per-match synchronizer; each requested provider endpoint reserves a
  daily quota unit before HTTP. Run `python -m app.live_cli --match-id 1`.
- Offline mock HTTP and idempotency tests; second Alembic migration.

## Limitations and follow-up
API-Football does not always expose native per-event IDs. Fingerprinting is a
best-effort deduplication strategy. If a provider corrects or deletes an event,
the old fingerprint may remain; reconciliation / reversals are **not**
implemented yet, so never directly trigger a one-time notification from the
presence of a new fingerprint without correction-aware processing.

The event payload's `assist` field is context-dependent: for goals it is
the assisting player, for substitutions it is the incoming player.
Player ratings require the separate fixture players endpoint; none are
synthesized. Statistics may be missing or reported as null for some leagues.

No public write endpoint exists for ingestion. Live polling at high frequency
would exceed the free plan; schedule detail requests for a small selection of
in-progress fixtures only after measuring daily budget. An event store and
reconciliation mechanism should precede notification delivery and WebSockets.

## Provider documentation
https://www.api-football.com/documentation-v3
https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide
