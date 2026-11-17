from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.providers.api_football import normalize_fixture
from app.services.ingestion import upsert_fixtures


def test_football_endpoints() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with Session(engine) as db:
            upsert_fixtures(db, [{
                "fixture": {"id": 5, "status": {"short": "FT"}},
                "teams": {"home": {"id": 1, "name": "Alpha"}, "away": {"id": 2, "name": "Beta"}},
                "league": {"id": 10, "name": "Test League"},
                "goals": {"home": 2, "away": 1},
            }][0:0])
            fixture = normalize_fixture({
                "fixture": {"id": 5, "status": {"short": "FT"}},
                "teams": {"home": {"id": 1, "name": "Alpha"}, "away": {"id": 2, "name": "Beta"}},
                "league": {"id": 10, "name": "Test League"},
                "goals": {"home": 2, "away": 1},
            })
            upsert_fixtures(db, [fixture])
            db.commit()
        client = TestClient(app)
        assert client.get("/api/v1/competitions").json()[0]["name"] == "Test League"
        assert client.get("/api/v1/matches").json()[0]["home_score"] == 2
        assert len(client.get("/api/v1/matches?status=FT").json()) == 1
        assert client.get("/api/v1/matches/999").status_code == 404
    finally:
        app.dependency_overrides.clear()
