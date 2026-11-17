"""Offline contract, normalization, U22 selection and REST API tests."""
from datetime import date

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Match, Player, PlayerMatchStats
from app.providers.api_football import ApiFootballClient, normalize_fixture
from app.services.ingestion import upsert_fixtures
from app.services.player_stats import (
    parse_nonnegative_int,
    parse_rating,
    upsert_fixture_players,
    upsert_player_profile,
)
from app.services.scouting import age_on, scouting_u22


def fixture(external_id: int, status: str = "FT") -> dict:
    return {
        "fixture": {
            "id": external_id,
            "date": f"2026-11-{external_id:02d}T12:00:00Z",
            "status": {"short": status},
        },
        "teams": {"home": {"id": 10, "name": "Home"},
                  "away": {"id": 20, "name": "Away"}},
        "league": {"id": 40, "name": "Test League"},
        "goals": {"home": 2, "away": 0},
    }


def group(*players: tuple[int, str, int | None, str | None, int | None, int | None]):
    return [{
        "team": {"id": 10},
        "players": [
            {"player": {"id": pid, "name": name},
             "statistics": [{
                 "games": {"minutes": minutes, "rating": rating, "position": "Attacker"},
                 "goals": {"total": goals, "assists": assists},
             }]}
            for pid, name, minutes, rating, goals, assists in players
        ],
    }]


def test_provider_fixture_players_and_profile() -> None:
    def mock(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fixtures/players":
            assert request.url.params["fixture"] == "100"
            return httpx.Response(
                200, json={"errors": [], "response": group(
                    (11, "Prospect", 60, "8.7", 1, 0),
                )},
            )
        assert request.url.path == "/players"
        assert request.url.params["id"] == "11"
        assert request.url.params["season"] == "2026"
        return httpx.Response(
            200, json={"errors": [], "response": [{
                "player": {
                    "id": 11, "name": "Prospect", "birth": {"date": "2007-09-20"},
                    "nationality": "Romania",
                }, "statistics": [{"games": {"position": "Attacker"}}],
            }]},
        )

    client = ApiFootballClient("mock-key", transport=httpx.MockTransport(mock))
    try:
        assert client.fetch_fixture_players(fixture_id=100)[0]["players"][0]["player"]["id"] == 11
        assert client.fetch_player_profile(player_id=11, season=2026)[0]["player"]["id"] == 11
    finally:
        client.close()


def test_rating_and_age_validation() -> None:
    assert parse_rating("8.75") == 8.75
    assert parse_rating("-") is None
    assert parse_rating("nan") is None
    assert parse_rating(12.3) is None
    assert parse_nonnegative_int("0") == 0
    assert parse_nonnegative_int(True) is None
    assert parse_nonnegative_int("-2") is None
    assert age_on(date(2004, 11, 18), date(2026, 11, 17)) == 21
    assert age_on(date(2004, 11, 17), date(2026, 11, 17)) == 22


def test_rating_ingestion_and_scouting_api() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for match_id in (10, 11, 12, 13):
            upsert_fixtures(db, [normalize_fixture(fixture(match_id))])
        matches = db.scalars(select(Match).order_by(Match.provider_id)).all()
        for index, match in enumerate(matches):
            incoming = group((22, "Established", 90, "7.6", 0, 0))
            if index == 0:
                incoming[0]["players"] += group(
                    (11, "Breakout", 60, "9.2", 1, 0),
                    (33, "Unknown DOB", 90, "9.9", 2, 0),
                    (44, "Too Old", 90, "9.9", 1, 0),
                    (55, "No Rating", 90, None, 1, 0),
                )[0]["players"]
            assert upsert_fixture_players(db, match, incoming) == len(incoming[0]["players"])
            assert upsert_fixture_players(db, match, incoming) == len(incoming[0]["players"])

        assert db.scalar(select(func.count()).select_from(PlayerMatchStats)) == 8
        for pid, born in (
            (11, "2007-09-20"),
            (22, "2006-02-02"),
            (44, "2004-01-01"),
            (55, "2005-06-02"),
        ):
            found = upsert_player_profile(db, [{
                "player": {"id": pid, "name": f"Name {pid}",
                           "birth": {"date": born}},
            }], pid)
            assert found is not None
        db.commit()

        established = scouting_u22(db, as_of=date(2026, 11, 17), min_minutes=300)
        emerging = scouting_u22(
            db, as_of=date(2026, 11, 17), category="emerging", min_minutes=300,
        )
        assert [entry["name"] for entry in established] == ["Name 22"]
        assert established[0]["minutes"] == 360
        assert established[0]["average_rating"] == 7.6
        assert established[0]["adjusted_rating"] < established[0]["average_rating"]
        assert [entry["name"] for entry in emerging] == ["Name 11"]
        assert emerging[0]["minutes"] == 60
        assert emerging[0]["sample_notice"] == "low_sample"
        first_match_id = matches[0].id
        prospect_id = db.scalar(select(Player.id).where(Player.provider_id == "11"))

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        ranked = client.get("/api/v1/scouting/u22",
                            params={"as_of": "2026-11-17", "category": "emerging"})
        assert ranked.status_code == 200
        assert ranked.json()["players"][0]["name"] == "Name 11"
        assert ranked.json()["league_adjusted"] is False
        match_players = client.get(f"/api/v1/matches/{first_match_id}/players")
        assert match_players.status_code == 200
        assert match_players.json()["rating_is_final"] is True
        assert len(match_players.json()["players"]) == 5
        assert client.get(f"/api/v1/players/{prospect_id}").json()["birth_date"] == "2007-09-20"
        assert client.get("/api/v1/players/999999").status_code == 404
        assert client.get("/api/v1/scouting/u22?category=invalid").status_code == 422
    finally:
        app.dependency_overrides.clear()
