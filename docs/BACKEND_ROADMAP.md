# FootScore Backend Roadmap

## Vision
Football live scores, fixtures, results, standings, lineups, substitutions, events, statistics and player ratings; cross-league U22 scouting; player alerts; AI-supported scouting and probabilistic match analysis.

## Data sourcing (blocking dependency)
Select a licensed football data provider after checking league coverage, historical depth, live event latency, player identifiers, lineups, xG, ratings, redistribution rights, costs and API rate limits. Do not scrape Flashscore. Provider adapters must normalize IDs and events; preserve provenance, timestamps and freshness. If a metric is unavailable, return null with coverage metadata rather than fabricate it.

## Architecture
Python 3.12 / FastAPI modular monolith, PostgreSQL, SQLAlchemy 2 + Alembic, Redis, Celery workers, WebSocket fanout, JWT authentication, FCM push, Docker, pytest and GitHub Actions. All secrets via environment variables. No frontend in this repository.

## Milestones
1. Foundation: app structure, config, health endpoints, Docker, lint/tests/CI, API versioning.
2. Football schema and ingestion: competitions, seasons, teams, players, memberships, fixtures, standings, event stream, player match statistics, ratings, provider mapping and backfill; idempotent upserts and stale-data alerts.
3. Live service: incremental updates, cache, WebSocket subscriptions, replay and deduplication; provider-appropriate polling; ordering and reconciliation.
4. Scouting: age-at-reference-date U22 eligibility, minutes/position/league-adjusted metrics, transparent leaderboards and filters. Separate low-sample breakout detection from established rankings; expose confidence intervals, sample size and data completeness.
5. Accounts: users, favorites, device tokens, notification preferences, goal/assist/final-rating event processing, idempotent delivery, quiet hours and audit trail.
6. AI scouting: explainable signals and evidence-grounded reports, with explicit uncertainty, small-sample penalties and human review.
7. Match analysis: time-split backtests and calibrated probabilities for 1X2, totals, BTTS, corners and cards where data permits; injuries/lineups/schedule/context as verified inputs. No guaranteed bets; disclose uncertainty and do not imply profit.
8. Hardening: provider contract tests, observability, queue retries, rate limits, access control, load tests, privacy and data retention.

## API outline
GET /health
GET /api/v1/competitions
GET /api/v1/competitions/{id}/standings
GET /api/v1/matches?status=live
GET /api/v1/matches/{id}
GET /api/v1/matches/{id}/events
GET /api/v1/players/{id}
GET /api/v1/scouting/u22?position=...
GET /api/v1/scouting/prospects
POST /api/v1/favorites/players/{id}
GET /api/v1/notifications
POST /api/v1/ai/scouting/report
POST /api/v1/ai/matches/analyze
WS /api/v1/live

## Acceptance criteria
All provider-sourced records carry provider, source ID, observed timestamp and last-updated timestamp. Live events are idempotent and replayable. Rankings show minutes, sample size and missingness. Alerts fire once per qualifying event and final ratings only after match completion. AI reports include evidence, probabilities and uncertainty. Automated tests and CI must pass before merge.

## Delivery policy
Work in feature/backend-foundation and merge into main through reviewed pull requests. No production keys in Git. Phase 1 foundation is not a functioning live-score/scouting platform until a data provider and later milestones are implemented.
