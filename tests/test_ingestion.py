from datetime import datetime, timezone

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Match
from app.providers.api_football import ApiFootballClient, normalize_fixture
from app.services.ingestion import upsert_fixtures


PAYLOAD = {
    "fixture": {"id": 42, "date": "2026-11-17T16:00:00Z", "status": {"short": "NS"}},
    "teams": {"home": {"id": 1}, "away": {"id": 2}},
    "league": {"id": 39},
    "goals": {"home": None, "away": None},
}


def test_normalize_fixture() -> None:
    result = normalize_fixture(PAYLOAD)
    assert result.source_id == "42"
    assert result.kickoff == datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
    assert result.home_score is None


def test_http_adapter_with_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-apisports-key"] == "test-key"
        assert request.url.params["league"] == "39"
        return httpx.Response(200, json={"response": [PAYLOAD], "errors": []})

    client = ApiFootballClient("test-key", transport=httpx.MockTransport(handler))
    try:
        assert client.fetch_fixtures(league=39, season=2026)[0].source_id == "42"
    finally:
        client.close()


def test_idempotent_ingestion() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        fixture = normalize_fixture(PAYLOAD)
        upsert_fixtures(db, [fixture])
        upsert_fixtures(db, [fixture])
        db.commit()
        assert db.scalar(select(func.count()).select_from(Match)) == 1
