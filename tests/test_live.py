import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Match, MatchEvent, Player, TeamMatchStat
from app.providers.api_football import ApiFootballClient, normalize_fixture
from app.services.ingestion import upsert_fixtures
from app.services.live import upsert_match_events, upsert_match_statistics


EVENTS = [
    {"time": {"elapsed": 24, "extra": None},
     "team": {"id": 1, "name": "Alpha"},
     "player": {"id": 101, "name": "Prospect"},
     "assist": {"id": 102, "name": "Helper"},
     "type": "Goal", "detail": "Normal Goal", "comments": None},
    {"time": {"elapsed": 55}, "team": {"id": 2},
     "player": {"id": 201, "name": "Defender"}, "assist": {"id": None},
     "type": "Card", "detail": "Red Card", "comments": None},
    {"time": {"elapsed": 65}, "team": {"id": 1},
     "player": {"id": 101, "name": "Prospect"},
     "assist": {"id": 103, "name": "Substitute"},
     "type": "subst", "detail": "Substitution 1", "comments": None},
]
STATS = [{"team": {"id": 1}, "statistics": [
    {"type": "Corner Kicks", "value": 4},
    {"type": "Ball Possession", "value": "54%"},
    {"type": "Expected Goals", "value": None},
]}]


def test_provider_detail_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fixture"] == "500"
        if request.url.path.endswith("/fixtures/events"):
            return httpx.Response(200, json={"response": EVENTS, "errors": []})
        return httpx.Response(200, json={"response": STATS, "errors": []})

    client = ApiFootballClient("test-key", transport=httpx.MockTransport(handler))
    try:
        assert len(client.fetch_events(fixture_id=500)) == 3
        assert client.fetch_statistics(fixture_id=500)[0]["statistics"][0]["value"] == 4
    finally:
        client.close()


def test_idempotent_live_data_and_read_api() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        upsert_fixtures(db, [normalize_fixture({
            "fixture": {"id": 500, "status": {"short": "2H"}},
            "teams": {"home": {"id": 1, "name": "Alpha"},
                      "away": {"id": 2, "name": "Beta"}},
            "league": {"id": 39, "name": "Example League"},
            "goals": {"home": 1, "away": 0},
        })])
        match = db.scalar(select(Match))
        for _ in range(2):
            assert upsert_match_events(db, match, EVENTS) == 3
            assert upsert_match_statistics(db, match, STATS) == 3
        db.commit()
        assert db.scalar(select(func.count()).select_from(MatchEvent)) == 3
        assert db.scalar(select(func.count()).select_from(TeamMatchStat)) == 3
        assert db.scalar(select(func.count()).select_from(Player)) == 3
        match_id = match.id

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        events = client.get(f"/api/v1/matches/{match_id}/events").json()
        assert [e["kind"] for e in events] == ["goal", "red_card", "substitution"]
        assert events[0]["player_id"] is not None
        stats = client.get(f"/api/v1/matches/{match_id}/statistics").json()
        assert {x["name"]: x["value"] for x in stats}["Ball Possession"] == "54%"
        assert {x["name"]: x["value"] for x in stats}["Expected Goals"] is None
        assert client.get("/api/v1/matches/99999/events").status_code == 404
    finally:
        app.dependency_overrides.clear()
