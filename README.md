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
- Read endpoints for match event timelines and team match statistics.
- User registration/login, authenticated favorites, notification preferences and inbox.
- One-time goal, assist and final rating alerts with optional FCM push outbox.

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
refunded on ambiguous failures. Do not run several schedulers for the same
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

This U22 leaderboard is a transparent sample-adjusted baseline, **not an AI
potential prediction**, and is not adjusted for league strength or position.
Only completed fixtures and players with verified DOB and provider rating count.
Authentication requires a strong `JWT_SECRET` environment variable (32+ characters).
Set it before using register/login with JWT. Run `alembic upgrade head` again.
Optional push dispatcher: install `pip install -e '.[push]'`, configure
Firebase Admin credentials, register a device from an authenticated client
and run `python -m app.push_cli` in a trusted worker environment.
See [notifications design](docs/NOTIFICATIONS_DESIGN.md).

The app is not yet production-ready: no live WebSocket, event correction
reconciliation, Redis, AI scouting, login rate limits or refresh tokens yet.

See [U22 design](docs/U22_SCOUTING_DESIGN.md).

See [roadmap](docs/BACKEND_ROADMAP.md) and [provider strategy](docs/FREE_API_STRATEGY.md).
