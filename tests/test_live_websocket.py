"""WebSocket snapshot and Redis pub/sub message delivery without real Redis."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import live_ws
from app.db import Base
from app.main import app
from app.models import Match
from app.providers.api_football import normalize_fixture
from app.services.ingestion import upsert_fixtures
from app.settings import settings


class PubSubStub:
    def __init__(self, match_id: int) -> None:
        self.match_id = match_id
        self.sent = False
        self.subscribed = False
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        assert channel == f"footscore:match:{self.match_id}"
        self.subscribed = True

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float):
        if not self.sent:
            self.sent = True
            return {"type": "message", "data": json.dumps({
                "id": 99, "match_id": self.match_id,
                "kind": "score_changed", "data": {"home_score": 2},
            })}
        import asyncio

        await asyncio.sleep(0.01)
        return None

    async def aclose(self) -> None:
        self.closed = True


class RedisStub:
    def __init__(self, pubsub: PubSubStub):
        self.value = pubsub
        self.closed = False

    def pubsub(self):
        return self.value

    async def aclose(self) -> None:
        self.closed = True


def test_live_socket_snapshot_and_incremental_update(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        upsert_fixtures(db, [normalize_fixture({
            "fixture": {"id": 123, "status": {"short": "1H"}},
            "league": {"id": 39},
            "teams": {"home": {"id": 1}, "away": {"id": 2}},
            "goals": {"home": 1, "away": 0},
        })])
        db.commit()
        match_id = db.scalar(select(Match.id))
    pubsub = PubSubStub(match_id)
    redis = RedisStub(pubsub)
    monkeypatch.setattr(settings, "redis_url", "redis://example:6379/0")
    monkeypatch.setattr(live_ws, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(live_ws, "SessionLocal", lambda: Session(engine))
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/live/matches/{match_id}") as socket:
            snapshot = socket.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["match_id"] == match_id
            assert snapshot["status"] == "1H"
            assert snapshot["last_update_id"] >= 1
            event = socket.receive_json()
            assert event == {
                "id": 99, "match_id": match_id,
                "kind": "score_changed", "data": {"home_score": 2},
            }
    assert pubsub.subscribed
    assert pubsub.closed
    assert redis.closed
