# FootScore Backend

Backend-only live football, U22 scouting and AI-analysis platform (work in progress).

## Implemented
- FastAPI with health, competition and fixture read routes.
- SQLAlchemy entities for competitions, teams, players, matches, events, statistics, standings, daily API quotas.
- Alembic initial migration, Docker Compose PostgreSQL, manual fixture import.
- API-Football adapter, idempotent fixture upserts and atomic daily quota reservations.
- Optional standalone periodic scheduler for a **small pilot league set**.

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
The app is not yet production-ready: no live WebSocket, provider event
sync, player scouting rankings, Redis cache, notifications or AI yet.

See [roadmap](docs/BACKEND_ROADMAP.md) and [provider strategy](docs/FREE_API_STRATEGY.md).
