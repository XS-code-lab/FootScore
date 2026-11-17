# FootScore Backend

Backend-only live football, U22 scouting and AI-analysis platform (work in progress).

## Implemented
- FastAPI with health, competition and fixture read routes.
- SQLAlchemy entities for competitions, teams, players, matches, events, statistics, standings, daily API quotas.
- Alembic initial migration, Docker Compose PostgreSQL, manual fixture import.
- API-Football adapter, idempotent fixture upserts and atomic daily quota reservations.
- Optional standalone periodic scheduler for a **small pilot league set**.
- Per-fixture goal/card/substitution/VAR event snapshots and team statistic ingestion.
- Per-fixture player ratings/minutes/goals/assists and targeted player profile enrichment.
- Read-only U22 established and emerging leaderboards with sample-size shrinkage.
- New position+competition peer-relative U22 discovery with coverage and uncertainty flags.
- Offline, audited-label ML experiment with chronological holdout and JSON coefficients.
- Safe historical fixture/player backfill planning, observed-response receipts and local coverage audits.
- Transactional rolling-60-second provider request budgets shared across backend workers.
- League-season capability metadata probes, explicitly not season-completeness proof.
- Next-season outcome observation proposals that explicitly refuse automatic ML labels.
- Read endpoints for match event timelines and team match statistics.
- User registration/login, authenticated favorites, notification preferences and inbox.
- One-time goal, assist and final rating alerts with optional FCM push outbox.
- Correction-aware live event reconciliation, retracted notifications, match update outbox.
- Redis Pub/Sub WebSocket stream plus cursor-based REST replay of match changes.

## Requirements
Python 3.11+ or Docker, an API-Football key for real requests. No API key is needed for offline tests.

## Local setup
```bash
python -m venv .venv
pip install -e '.[dev]'
alembic upgrade head
pytest -q
uvicorn app.main:app --reload
```

Use `DATABASE_URL` in `.env` for persistent DB; development default is SQLite.
Swagger: http://127.0.0.1:8000/docs

## Postgres with Compose
Copy `.env.example` to `.env`; replace POSTGRES_PASSWORD with a **URL-safe random string**.
```bash
docker compose up -d db
docker compose run --rm api alembic upgrade head
docker compose up -d api
```
Optional, after setting API_FOOTBALL_KEY and a pilot SYNC_LEAGUES list:
```bash
docker compose --profile sync up -d scheduler
```
For a one-off import:
```bash
python -m app.cli --league 39 --season 2026
```

The free API budget defaults to 100 requests/day with a reserve of 10.
Requests are charged locally **before** calling the provider and are never
refunded on ambiguous failures. All in-app provider callers share the
same database-backed daily and rolling 60-second guardrails (default 8/min,
10/day reserved). Calls made outside FootScore using the same provider key
are not counted. Do not run several schedulers for the same
league. The pilot interval defaults to six hours; actual quota use depends
on the number of leagues and additional API clients sharing the key.
To fetch events and team statistics for one *existing* FootScore match (costs
two provider requests), run: `python -m app.live_cli --match-id 1`.
This is an explicit command rather than automatic polling to conserve quota.
For a selected fixture's player stats (one additional provider request):
```bash
python -m app.players_cli fixture --match-id 1
```
To enrich a player with verified birth date (another provider request):
```bash
python -m app.players_cli profile --provider-player-id 123 --season 2026
```

Additional API endpoints:
- `GET /api/v1/matches/{match_id}/players`
- `GET /api/v1/players/{player_id}`
- `GET /api/v1/scouting/u22?category=established`
- `GET /api/v1/scouting/u22?category=emerging`
- `GET /api/v1/scouting/u22/discovery?category=emerging`
- `GET /api/v1/scouting/u22/reports/{player_id}`

This U22 leaderboard is a transparent sample-adjusted baseline, **not an AI
potential prediction**, and is not adjusted for league strength or position.
Only completed fixtures and players with verified DOB and provider rating count.
Authentication requires a strong `JWT_SECRET` environment variable (32+ characters).
Generate it with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Use HTTPS and add a rate limit before exposing account routes publicly.
Set it before using register/login with JWT. Run `alembic upgrade head` again.
Optional push dispatcher: install `pip install -e '.[push]'`, configure
Firebase Admin credentials, register a device from an authenticated client
and run `python -m app.push_cli` in a trusted worker environment.
See [notifications design](docs/NOTIFICATIONS_DESIGN.md).

For the optional Redis live stream, set `REDIS_URL=redis://redis:6379/0`
and start the dedicated publisher:
```bash
docker compose --profile live up -d redis api live-publisher
```
The public stream is `ws://localhost:8000/api/v1/live/matches/{match_id}`.
To catch up after a disconnect use `GET /api/v1/matches/{match_id}/updates?after_id=0`.
Live updates are published **only after ingested data is committed**; this
stream does not poll API-Football for free. See
[Live stream design](docs/LIVE_STREAM_DESIGN.md).

This remains a development baseline: provider event IDs and VAR corrections
need more validation, and Redis publisher coordination, backpressure, API
rate limiting, stronger auth/account lifecycle and trained AI scouting remain pending.
Real device push and end-to-end production deployment are unverified.

See [U22 design](docs/U22_SCOUTING_DESIGN.md) and
[position/league cohort discovery v2](docs/U22_DISCOVERY_V2.md).
New scouting discovery reads existing match data without consuming new provider
quota. Scores are **not probabilities or predictions**; only within-competition,
within-position comparisons are supported when at least five peers are present.
No league-strength normalization or validated real-data ML model exists yet.

### Historical data audit (v0.9)
Use a LOCAL competition ID and a date window. The default command is a
**dry run** (zero external provider calls):
```bash
python -m app.historical_cli --competition-id 1 --start 2024-07-01 --end 2025-06-30 --max-requests 5
```
To actually fetch the selected fixture-player responses, configure the
provider key and explicitly add `--execute`. One fixture costs one reserved
request, and zero-player results are flagged as **unknown coverage**, not
zero playing time. Use `--report` for local coverage status, not season proof.

To inspect observed next-season minutes without generating ML labels:
```bash
python -m app.ml.cli propose-outcomes --input data/ml/unverified.jsonl --output data/ml/outcome-review.jsonl
```
This leaves all `target` values unset and demands manual, independently
sourced auditing before any ML training. See
[historical backfill and data coverage](docs/HISTORICAL_BACKFILL.md).

### Offline ML research (v0.8)
Install the optional research dependencies: `pip install -e '.[dev,ml]'`.
Export **unverified, unlabeled** feature snapshots using the local database:
```bash
mkdir -p data/ml
python -m app.ml.cli export --cutoff 2024-06-30 --competition-id 1 --position attacker --output data/ml/unverified.jsonl
```
A retrospective snapshot is **not** proof that every feature was knowable
at that cutoff. Labels must be externally audited with complete next-season
competition tracking and timestamped point-in-time feature evidence.
Only then train on the curated JSONL file:
```bash
python -m app.ml.cli train --input data/ml/verified.jsonl --output data/ml/experiment.json
```
The target is next-season **900+ recorded minutes in the same competition**,
not talent or future career success. No true historical labels or real trained
model are bundled; experiment artifacts are always non-production and never
exposed via the public API. See [research protocol](docs/ML_RESEARCH_PROTOCOL.md).

See [roadmap](docs/BACKEND_ROADMAP.md) and [provider strategy](docs/FREE_API_STRATEGY.md).
