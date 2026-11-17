# FootScore Backend

Backend-only football live data + U22 scouting + AI match-analysis platform.

## Status
Phase 1 foundation and the **initial phase 2 football ingestion slice** are implemented.
Not production-ready: no real ingestion schedule, migrations, live WebSocket stream,
scouting ranking, notifications or AI predictions yet.

## Local setup
```bash
python -m venv .venv
pip install -e '.[dev]'
pytest -q
uvicorn app.main:app --reload
```

Swagger API: http://127.0.0.1:8000/docs

## First football endpoints
- `GET /health`
- `GET /api/v1/health`
- `GET /api/v1/competitions`
- `GET /api/v1/matches`
- `GET /api/v1/matches/{match_id}`

Configure `DATABASE_URL` and `API_FOOTBALL_KEY` in a local `.env`.
Provider fixtures can be fetched with `ApiFootballClient` and persisted through
`upsert_fixtures`; fetching is **not** automatically scheduled.
Database tables must be provisioned separately before calling database endpoints;
migrations are planned next. Never commit real API keys.

See [the phased development plan](docs/BACKEND_ROADMAP.md).
