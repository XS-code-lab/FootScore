"""Snapshot reconciliation, notification corrections, outbox and API replay."""
import json
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import (
    FavoritePlayer, LiveUpdate, Match, MatchEvent, Notification,
    Player, PushDelivery, PushDevice, User,
)
from app.providers.api_football import normalize_fixture
from app.services.ingestion import upsert_fixtures
from app.services.live import upsert_match_events
from app.services.live_updates import drain_outbox, match_snapshot


def _fixture() -> dict:
    return {
        "fixture": {"id": 700, "date": "2026-11-17T19:00:00Z",
                    "status": {"short": "1H"}},
        "teams": {"home": {"id": 1, "name": "Alpha"},
                  "away": {"id": 2, "name": "Beta"}},
        "league": {"id": 39, "name": "Example"},
        "goals": {"home": 1, "away": 0},
    }


def _goal(assist_id: int = 12, detail: str = "Normal Goal") -> dict:
    return {
        "time": {"elapsed": 25},
        "team": {"id": 1},
        "player": {"id": 11, "name": "Striker"},
        "assist": {"id": assist_id, "name": f"Helper {assist_id}"},
        "type": "Goal", "detail": detail, "comments": None,
    }


def _card() -> dict:
    return {
        "time": {"elapsed": 42},
        "team": {"id": 2},
        "player": {"id": 21, "name": "Defender"},
        "assist": {"id": None},
        "type": "Card", "detail": "Yellow Card", "comments": None,
    }


class FakeRedis:
    def __init__(self):
        self.messages = []

    def publish(self, channel: str, data: str):
        self.messages.append((channel, json.loads(data)))
        return 1


def test_reconciliation_push_suppression_and_replay() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        upsert_fixtures(db, [normalize_fixture(_fixture())])
        match = db.scalar(select(Match))
        # Seed monitored profiles without emitting historical event alerts.
        db.add_all([
            Player(name=f"Player {pid}", provider="api-football", provider_id=pid)
            for pid in ("11", "12", "13")
        ])
        db.flush()
        players = {p.provider_id: p for p in db.scalars(select(Player)).all()}
        user = User(email="realtime@example.com", password_hash="unused")
        db.add(user)
        db.flush()
        for pid in ("11", "12", "13"):
            db.add(FavoritePlayer(user_id=user.id, player_id=players[pid].id))
        db.add(PushDevice(
            user_id=user.id, registration_token="device-" + "a" * 40,
        ))
        db.commit()
        event_count_before = db.scalar(select(func.count()).select_from(LiveUpdate))
        upsert_match_events(db, match, [_goal(12)], reconcile=True)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 2
        initial_updates = db.scalar(select(func.count()).select_from(LiveUpdate))
        upsert_match_events(db, match, [_goal(12)], reconcile=True)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification)) == 2
        assert db.scalar(select(func.count()).select_from(LiveUpdate)) == initial_updates

        # Changing just the assister must not re-notify the same scorer.
        revised = _goal(13)
        upsert_match_events(db, match, [revised], reconcile=True)
        db.commit()
        notifications = db.scalars(select(Notification)).all()
        assert len(notifications) == 3
        old_assist = next(
            x for x in notifications
            if x.kind == "assist" and x.player_id == players["12"].id
            and x.invalidated_at is not None
        )
        assert old_assist.invalidated_at is not None
        canceled = db.scalar(select(PushDelivery).where(
            PushDelivery.notification_id == old_assist.id,
        ))
        assert canceled.status == "canceled"
        assert db.scalar(select(func.count()).select_from(
            Notification
        ).where(Notification.kind == "goal")) == 1

        canceled_goal = _goal(13, "Goal Cancelled")
        upsert_match_events(db, match, [canceled_goal], reconcile=True)
        db.commit()
        assert db.scalar(select(func.count()).select_from(
            Notification
        ).where(Notification.invalidated_at.is_(None))) == 0
        assert db.scalar(select(func.count()).select_from(
            PushDelivery
        ).where(PushDelivery.status == "pending")) == 0

        # Finally the canceled event disappears from the provider snapshot.
        upsert_match_events(db, match, [_card()], reconcile=True)
        db.commit()
        assert db.scalar(select(func.count()).select_from(
            MatchEvent
        ).where(MatchEvent.is_active.is_(True))) == 1
        snap = match_snapshot(db, match.id)
        assert snap["events"][0]["kind"] == "yellow_card"
        assert any(
            x.kind == "event_retracted"
            for x in db.scalars(select(LiveUpdate)).all()
        )
        assert initial_updates > event_count_before

        fake = FakeRedis()
        count = drain_outbox(db, fake)
        assert count == db.scalar(select(func.count()).select_from(LiveUpdate))
        assert drain_outbox(db, fake) == 0
        db.commit()
        assert all(channel == f"footscore:match:{match.id}"
                   for channel, _ in fake.messages)
        assert all(isinstance(message["id"], int) for _, message in fake.messages)
        match_id = match.id

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        stream = client.get(f"/api/v1/matches/{match_id}/updates")
        assert stream.status_code == 200
        updates = stream.json()
        assert updates[-1]["kind"] == "event_retracted"
        assert client.get(
            f"/api/v1/matches/{match_id}/updates",
            params={"after_id": updates[-1]["id"]},
        ).json() == []
        assert client.get("/api/v1/matches/999/updates").status_code == 404
        assert client.get("/api/v1/matches/1/updates?limit=201").status_code == 422
        events = client.get(f"/api/v1/matches/{match_id}/events").json()
        assert len(events) == 1
    finally:
        app.dependency_overrides.clear()


def test_empty_snapshot_is_safe_by_default() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        upsert_fixtures(db, [normalize_fixture(_fixture())])
        match = db.scalar(select(Match))
        upsert_match_events(db, match, [_goal()], reconcile=True)
        db.flush()
        assert db.scalar(select(func.count()).select_from(
            MatchEvent
        ).where(MatchEvent.is_active.is_(True))) == 1
        upsert_match_events(db, match, [], reconcile=True)
        db.flush()
        assert db.scalar(select(func.count()).select_from(
            MatchEvent
        ).where(MatchEvent.is_active.is_(True))) == 1
        upsert_match_events(db, match, [], reconcile=True, allow_empty=True)
        db.flush()
        assert db.scalar(select(func.count()).select_from(
            MatchEvent
        ).where(MatchEvent.is_active.is_(True))) == 0
