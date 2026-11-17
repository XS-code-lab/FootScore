"""Full offline pilot pipeline: provider HTTP -> ingestion -> API -> alerts.

No external service, credentials, or real API-Football requests are used.
"""
from copy import deepcopy
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import (
    Competition, HistoricalPlayerFetch, LiveUpdate, Match, MatchEvent,
    Notification, Player, PlayerMatchStats, ProviderQuota, ProviderRequestLog,
)
from app.providers.api_football import (
    ApiFootballClient, ProviderError, ProviderRateLimitError,
)
from app.services import league_probe, sync, sync_live, sync_players
from app.services.live_updates import drain_outbox, match_snapshot
from app.services.provider_guardrails import ProviderRateLimited
from app.settings import settings

FIXTURE = {
    "fixture": {
        "id": 500, "date": "2026-11-16T19:00:00Z",
        "status": {"short": "2H"},
    },
    "league": {"id": 39, "name": "Pilot League"},
    "teams": {
        "home": {"id": 10, "name": "Alpha"},
        "away": {"id": 20, "name": "Beta"},
    },
    "goals": {"home": 1, "away": 0},
}

GOAL = {
    "time": {"elapsed": 34, "extra": None},
    "team": {"id": 10},
    "player": {"id": 101, "name": "Young Scorer"},
    "assist": {"id": None},
    "type": "Goal", "detail": "Normal Goal", "comments": None,
}


class FakeRedis:
    def __init__(self):
        self.messages = []

    def publish(self, channel: str, message: str):
        self.messages.append((channel, message))
        return 1


def _setup(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    responses = {"finished": False, "goal_canceled": False, "calls": []}

    def route(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-apisports-key"] == "synthetic-test-only"
        path = request.url.path
        responses["calls"].append(path)
        if path == "/leagues":
            response = [{
                "league": {"id": 39},
                "seasons": [{
                    "year": 2026, "coverage": {
                        "fixtures": {
                            "events": True,
                            "statistics_players": True,
                            "statistics_fixtures": True,
                            "lineups": False,
                        },
                    },
                }],
            }]
        elif path == "/fixtures":
            payload = deepcopy(FIXTURE)
            if responses["finished"]:
                payload["fixture"]["status"]["short"] = "FT"
            response = [payload]
        elif path == "/fixtures/players":
            response = [{
                "team": {"id": 10},
                "players": [{
                    "player": {"id": 101, "name": "Young Scorer"},
                    "statistics": [{
                        "games": {
                            "minutes": 90, "rating": "8.2",
                            "position": "Attacker",
                        },
                        "goals": {"total": 1, "assists": 0},
                        "shots": {"on": 2},
                    }],
                }],
            }]
        elif path == "/players":
            response = [{
                "player": {
                    "id": 101, "name": "Young Scorer",
                    "birth": {"date": "2007-07-07"},
                    "nationality": "Romania",
                },
                "statistics": [{"games": {"position": "Attacker"}}],
            }]
        elif path == "/fixtures/events":
            payload = deepcopy(GOAL)
            if responses["goal_canceled"]:
                payload["detail"] = "Goal Cancelled"
            response = [payload]
        elif path == "/fixtures/statistics":
            response = [{
                "team": {"id": 10},
                "statistics": [{"type": "Corner Kicks", "value": 3}],
            }]
        else:
            raise AssertionError(f"Unexpected simulated HTTP endpoint: {path}")
        return httpx.Response(200, json={"errors": [], "response": response})

    transport = httpx.MockTransport(route)

    def client_factory(key: str, *, base_url: str):
        return ApiFootballClient(key, base_url=base_url, transport=transport)

    for module in (sync, sync_live, sync_players, league_probe):
        monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(module, "ApiFootballClient", client_factory)
    monkeypatch.setattr(settings, "api_football_key", "synthetic-test-only")
    monkeypatch.setattr(settings, "provider_daily_limit", 100)
    monkeypatch.setattr(settings, "provider_reserve", 10)
    monkeypatch.setattr(settings, "provider_per_minute_limit", 8)

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return engine, factory, responses


def test_full_pilot_data_flow_through_public_api(monkeypatch) -> None:
    engine, factory, responses = _setup(monkeypatch)
    try:
        capability = league_probe.probe_league(39, 2026)
        assert capability["capabilities"]["statistics_players"] is True
        assert capability["usable_for_ml_negative_labels"] is False

        assert sync.sync_league(39, 2026).imported == 1
        with factory() as db:
            match = db.scalar(select(Match))
            competition = db.scalar(select(Competition))
            assert match.status == "2H"
            match_id = match.id
            competition_id = competition.id

        assert sync_players.sync_fixture_players(match_id).imported == 1
        assert sync_players.sync_player_profile(101, 2026) is not None

        with factory.begin() as db:
            from app.models import FavoritePlayer, User

            player = db.scalar(select(Player).where(Player.provider_id == "101"))
            assert player.birth_date == date(2007, 7, 7)
            player_id = player.id
            user = User(email="e2e@example.com", password_hash="test-only")
            db.add(user)
            db.flush()
            db.add(FavoritePlayer(user_id=user.id, player_id=player_id))

        detail = sync_live.sync_match_details(match_id)
        assert detail.events == 1
        assert detail.statistics == 1

        responses["finished"] = True
        assert sync.sync_league(39, 2026).imported == 1
        with factory() as db:
            notes = db.scalars(select(Notification).order_by(Notification.id)).all()
            assert [note.kind for note in notes] == ["goal", "final_rating"]
            assert db.scalar(select(Match.status).where(Match.id == match_id)) == "FT"
            assert db.scalar(select(func.count()).select_from(ProviderRequestLog)) == 7
            assert db.scalar(select(ProviderQuota.used)) == 7
            assert db.scalar(select(func.count()).select_from(HistoricalPlayerFetch)) == 1
            assert db.scalar(select(func.count()).select_from(PlayerMatchStats)) == 1
            initial_updates = db.scalar(select(func.count()).select_from(LiveUpdate))
            snapshot = match_snapshot(db, match_id)
            assert snapshot["status"] == "FT"
            assert snapshot["events"][0]["kind"] == "goal"
            assert snapshot["players"][0]["rating"] == 8.2
            assert snapshot["statistics"][0]["value"] == 3

        with TestClient(app) as api:
            assert api.get("/api/v1/health").json() == {"status": "ok"}
            assert api.get("/ready").json() == {
                "status": "ready", "database": "connected",
            }
            matches = api.get("/api/v1/matches").json()
            assert len(matches) == 1
            assert matches[0]["competition_id"] == competition_id
            players = api.get(f"/api/v1/matches/{match_id}/players").json()
            assert players["rating_is_final"] is True
            assert players["players"][0]["rating"] == 8.2
            emerging = api.get("/api/v1/scouting/u22", params={
                "category": "emerging", "as_of": "2026-11-17",
            })
            assert emerging.status_code == 200
            assert [p["name"] for p in emerging.json()["players"]] == [
                "Young Scorer",
            ]
            discovery = api.get("/api/v1/scouting/u22/discovery", params={
                "category": "emerging", "as_of": "2026-11-17",
            }).json()
            assert discovery["groups"][0]["players"][0]["relative_score"] is None
            assert not discovery["groups"][0]["cohort_ready"]

            replay = api.get(f"/api/v1/matches/{match_id}/updates").json()
            assert len(replay) == initial_updates
            assert api.get(
                f"/api/v1/matches/{match_id}/updates",
                params={"after_id": replay[-1]["id"]},
            ).json() == []

        responses["goal_canceled"] = True
        assert sync_live.sync_match_details(
            match_id, events=True, statistics=False,
        ).events == 1
        with factory() as db:
            event = db.scalar(select(MatchEvent))
            assert event.kind == "goal_disallowed"
            invalidated = db.scalar(select(Notification).where(
                Notification.kind == "goal",
            ))
            assert invalidated.invalidated_at is not None
            assert db.scalar(select(func.count()).select_from(ProviderRequestLog)) == 8
            assert db.scalar(select(ProviderQuota.used)) == 8
            assert db.scalar(select(func.count()).select_from(LiveUpdate)) > initial_updates

        with pytest.raises(ProviderRateLimited):
            sync.sync_league(39, 2026)
        assert len(responses["calls"]) == 8
        fake_redis = FakeRedis()
        with factory.begin() as db:
            sent = drain_outbox(db, fake_redis)
            assert sent > 0
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(
                LiveUpdate
            ).where(LiveUpdate.published_at.is_(None))) == 0
        assert all(channel == f"footscore:match:{match_id}"
                   for channel, _ in fake_redis.messages)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_provider_429_does_not_automatically_retry_or_refund(monkeypatch) -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fail(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(429, headers={"retry-after": "45"}, json={
            "errors": {"requests": "Too many requests"}, "response": [],
        })

    def mocked_client(api_key: str, *, base_url: str):
        return ApiFootballClient(
            api_key, base_url=base_url, transport=httpx.MockTransport(fail),
        )

    monkeypatch.setattr(sync, "SessionLocal", factory)
    monkeypatch.setattr(sync, "ApiFootballClient", mocked_client)
    monkeypatch.setattr(settings, "api_football_key", "test-only")
    with pytest.raises(ProviderRateLimitError) as exc:
        sync.sync_league(39, 2026)
    assert exc.value.retry_after_seconds == 45
    assert exc.value.status_code == 429
    assert calls == ["/fixtures"]
    with factory() as db:
        assert db.scalar(select(ProviderQuota.used)) == 1
        assert db.scalar(select(func.count()).select_from(ProviderRequestLog)) == 1
        assert db.scalar(select(func.count()).select_from(Match)) == 0
    engine.dispose()


@pytest.mark.parametrize(
    ("payload", "status", "expected"),
    [
        ({"errors": ["quota exhausted"], "response": []}, 200, ProviderError),
        ({"errors": [], "response": "wrong shape"}, 200, ProviderError),
        ({"errors": [], "response": [1, 2]}, 200, ProviderError),
        ({"errors": [], "response": []}, 503, ProviderError),
    ],
)
def test_provider_rejects_broken_responses(payload, status, expected) -> None:
    client = ApiFootballClient(
        "mock-only",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=payload),
        ),
    )
    try:
        with pytest.raises(expected):
            client.fetch_events(fixture_id=500)
    finally:
        client.close()
