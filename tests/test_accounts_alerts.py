"""Authenticated favorites, event inbox, final rating, tenant isolation and push queue."""
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import (
    Match, Notification, Player, PushDelivery, User,
)
from app.providers.api_football import normalize_fixture
from app.services.ingestion import upsert_fixtures
from app.services.live import upsert_match_events
from app.services.player_stats import upsert_fixture_players
from app.services.push import dispatch_pending
from app.settings import settings


def _fixture(status: str) -> dict:
    return {
        "fixture": {
            "id": 555, "date": "2026-11-17T19:00:00Z",
            "status": {"short": status},
        },
        "teams": {
            "home": {"id": 100, "name": "Home"},
            "away": {"id": 200, "name": "Away"},
        },
        "league": {"id": 39, "name": "Test"},
        "goals": {"home": 1, "away": 0},
    }


def _events() -> list[dict]:
    return [{
        "time": {"elapsed": 44}, "team": {"id": 100},
        "player": {"id": 11, "name": "Scorer"},
        "assist": {"id": 12, "name": "Creator"},
        "type": "Goal", "detail": "Normal Goal", "comments": None,
    }]


def _players() -> list[dict]:
    return [{
        "team": {"id": 100}, "players": [
            {"player": {"id": 11, "name": "Scorer"},
             "statistics": [{"games": {"minutes": 90, "rating": "7.6"},
                             "goals": {"total": 1, "assists": 0}}]},
            {"player": {"id": 12, "name": "Creator"},
             "statistics": [{"games": {"minutes": 90, "rating": "8.5"},
                             "goals": {"total": 0, "assists": 1}}]},
        ],
    }]


def test_auth_favorites_notifications_and_delivery(monkeypatch) -> None:
    monkeypatch.setattr(settings, "jwt_secret", SecretStr("a-long-test-secret-" * 4))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        upsert_fixtures(db, [normalize_fixture(_fixture("1H"))])
        match = db.scalar(select(Match))
        assert upsert_fixture_players(db, match, _players()) == 2
        db.commit()
        player_ids = {
            p.provider_id: p.id for p in db.scalars(select(Player)).all()
        }
        match_id = match.id

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        assert client.get("/api/v1/favorites/players").status_code in {401, 403}
        assert client.post("/api/v1/auth/register", json={
            "email": "invalid@example.com", "password": "short",
        }).status_code == 422
        assert client.post("/api/v1/auth/register", json={
            "email": "  Scout@Example.COM ", "password": "LongAndSecure1!",
        }).status_code == 201
        assert client.post("/api/v1/auth/register", json={
            "email": "scout@example.com", "password": "LongAndSecure1!",
        }).status_code == 409
        assert client.post("/api/v1/auth/register", json={
            "email": "other@example.com", "password": "OtherPass123!",
        }).status_code == 201
        assert client.post("/api/v1/auth/login", json={
            "email": "scout@example.com", "password": "incorrect",
        }).status_code == 401
        login = client.post("/api/v1/auth/login", json={
            "email": "SCOUT@example.com", "password": "LongAndSecure1!",
        })
        assert login.status_code == 200
        token = login.json()["access_token"]
        scout = {"Authorization": f"Bearer {token}"}
        second_login = client.post("/api/v1/auth/login", json={
            "email": "other@example.com", "password": "OtherPass123!",
        })
        other = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
        assert client.get("/api/v1/auth/me", headers=scout).json()["email"] == (
            "scout@example.com"
        )
        assert client.get("/api/v1/auth/me", headers={
            "Authorization": "Bearer bogus",
        }).status_code == 401

        for player_id in player_ids.values():
            assert client.put(
                f"/api/v1/favorites/players/{player_id}", headers=scout,
            ).status_code == 200
            assert client.put(
                f"/api/v1/favorites/players/{player_id}", headers=scout,
            ).status_code == 200
        assert len(client.get("/api/v1/favorites/players", headers=scout).json()) == 2
        assert client.get("/api/v1/favorites/players", headers=other).json() == []
        assert client.put(
            "/api/v1/favorites/players/999999", headers=scout,
        ).status_code == 404
        assert client.put(
            f"/api/v1/favorites/players/{player_ids['11']}", headers=other,
        ).status_code == 200
        assert client.patch("/api/v1/notifications/preferences", headers=other,
                            json={"goals": False, "final_rating": False}).status_code == 200
        update = client.patch("/api/v1/notifications/preferences", headers=scout,
                              json={"min_final_rating": 8.0})
        assert update.json()["min_final_rating"] == 8
        assert client.patch("/api/v1/notifications/preferences", headers=scout,
                            json={"goals": None}).status_code == 422

        device_token = "mock-device-token-" + "x" * 30
        assert client.post("/api/v1/notifications/devices", headers=scout,
                           json={"token": device_token}).status_code == 201
        assert client.post("/api/v1/notifications/devices", headers=other,
                           json={"token": device_token}).status_code == 409

        with Session(engine) as db:
            match = db.get(Match, match_id)
            assert upsert_match_events(db, match, _events()) == 1
            assert upsert_match_events(db, match, _events()) == 1
            db.commit()
            assert db.scalar(select(func.count()).select_from(Notification)) == 2
            assert db.scalar(select(func.count()).select_from(PushDelivery)) == 2

        alert_list = client.get("/api/v1/notifications", headers=scout).json()
        assert {alert["type"] for alert in alert_list} == {"goal", "assist"}
        assert client.get("/api/v1/notifications", headers=other).json() == []
        assert client.post(
            f"/api/v1/notifications/{alert_list[0]['id']}/read",
            headers=other,
        ).status_code == 404
        assert client.post(
            f"/api/v1/notifications/{alert_list[0]['id']}/read",
            headers=scout,
        ).status_code == 200
        assert len(client.get(
            "/api/v1/notifications?unread_only=true", headers=scout,
        ).json()) == 1

        with Session(engine) as db:
            upsert_fixtures(db, [normalize_fixture(_fixture("FT"))])
            upsert_fixtures(db, [normalize_fixture(_fixture("FT"))])
            db.commit()
            assert db.scalar(select(func.count()).select_from(Notification)) == 3
        notes = client.get("/api/v1/notifications", headers=scout).json()
        final = [note for note in notes if note["type"] == "final_rating"]
        assert len(final) == 1
        assert final[0]["player_id"] == player_ids["12"]
        assert final[0]["data"]["rating"] == 8.5

        with Session(engine) as db:
            first = dispatch_pending(db, lambda token, note: None)
            assert first == {"sent": 3, "failed": 0}
            assert dispatch_pending(db, lambda token, note: None) == {
                "sent": 0, "failed": 0,
            }
            db.commit()
            assert db.scalar(select(func.count()).select_from(
                PushDelivery
            ).where(PushDelivery.status == "sent")) == 3

        assert client.delete(
            f"/api/v1/favorites/players/{player_ids['11']}", headers=scout,
        ).status_code == 204
        assert len(client.get("/api/v1/favorites/players", headers=scout).json()) == 1
        assert client.request("DELETE", "/api/v1/notifications/devices", headers=scout,
                              json={"token": device_token}).status_code == 204
    finally:
        app.dependency_overrides.clear()


def test_push_retry_and_event_filter() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="unit@example.com", password_hash="not-a-real-hash")
        db.add(user)
        upsert_fixtures(db, [normalize_fixture(_fixture("1H"))])
        db.flush()
        match = db.scalar(select(Match))
        upsert_fixture_players(db, match, _players())
        db.commit()
        scorer = db.scalar(select(Player).where(Player.provider_id == "11"))
        from app.models import FavoritePlayer, PushDevice

        db.add(FavoritePlayer(user_id=user.id, player_id=scorer.id))
        db.add(PushDevice(user_id=user.id, registration_token="test-" + "z" * 32))
        db.commit()
        # Own goals, although represented as Goal by the provider, must not
        # be treated as a positive scorer goal alert.
        own = _events()[0].copy()
        own["detail"] = "Own Goal"
        upsert_match_events(db, match, [own])
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 0
        upsert_match_events(db, match, _events())
        db.commit()

        def fail(token, note):
            raise RuntimeError("transient")

        assert dispatch_pending(db, fail, max_attempts=2)["failed"] == 1
        assert dispatch_pending(db, fail, max_attempts=2)["failed"] == 1
        assert dispatch_pending(db, fail, max_attempts=2) == {
            "sent": 0, "failed": 0,
        }
        assert db.scalar(select(PushDelivery.status)) == "failed"
