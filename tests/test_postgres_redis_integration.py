"""Real PostgreSQL + Redis smoke, opt-in for a disposable CI database only.

The usual SQLite/mock test suite skips these tests. The integration job starts
isolated service containers, runs Alembic, and explicitly enables this module.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
import time
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import func, inspect, select, text

from app.db import SessionLocal, engine
from app.main import app
from app.models import Match, ProviderQuota, ProviderRequestLog
from app.providers.api_football import normalize_fixture
from app.services.ingestion import upsert_fixtures
from app.services.live_updates import drain_outbox
from app.services.provider_guardrails import ProviderRateLimited, reserve_provider_call
from app.settings import settings

pytestmark = pytest.mark.skipif(
    os.getenv("FOOTSCORE_INTEGRATION_TESTS") != "1",
    reason="Requires disposable migrated PostgreSQL and Redis services",
)


def test_migrations_and_concurrent_postgres_quota_reservations() -> None:
    assert engine.dialect.name == "postgresql"
    tables = set(inspect(engine).get_table_names())
    assert {
        "alembic_version", "matches", "live_updates", "provider_quota",
        "provider_request_log", "provider_rate_locks",
        "historical_player_fetches", "league_coverage_probes",
    } <= tables
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0007_provider_guardrails"

    workers = 6
    gate = Barrier(workers)

    def reserve() -> int | None:
        gate.wait(timeout=10)
        try:
            with SessionLocal.begin() as db:
                return reserve_provider_call(
                    db,
                    provider="ci-integration-serial",
                    minute_limit=2,
                    daily_limit=100,
                    daily_reserve=0,
                )
        except ProviderRateLimited:
            return None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _: reserve(), range(workers)))
    assert sorted(value for value in results if value is not None) == [1, 2]
    assert results.count(None) == workers - 2

    with SessionLocal() as db:
        assert db.scalar(select(ProviderQuota.used).where(
            ProviderQuota.provider == "ci-integration-serial",
        )) == 2
        assert db.scalar(select(func.count()).select_from(
            ProviderRequestLog
        ).where(ProviderRequestLog.provider == "ci-integration-serial")) == 2


def test_redis_pubsub_durable_outbox_and_http_replay() -> None:
    assert settings.redis_url
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    assert redis.ping() is True
    fixture = normalize_fixture({
        "fixture": {
            "id": 990123, "date": "2026-11-17T18:00:00Z",
            "status": {"short": "1H"},
        },
        "league": {"id": 990, "name": "CI Integration League"},
        "teams": {
            "home": {"id": 9901, "name": "CI Home"},
            "away": {"id": 9902, "name": "CI Away"},
        },
        "goals": {"home": 1, "away": 0},
    })
    with SessionLocal.begin() as db:
        upsert_fixtures(db, [fixture])
        db.flush()
        match = db.scalar(select(Match).where(
            Match.provider_id == "990123",
        ))
        match_id = match.id

    channel = f"footscore:match:{match_id}"
    pubsub = redis.pubsub()
    try:
        pubsub.subscribe(channel)
        with SessionLocal.begin() as db:
            count = drain_outbox(db, redis)
        assert count >= 1
        # PubSub subscription is active before publishing; receive real Redis data.
        deadline = time.monotonic() + 5
        message = None
        while message is None and time.monotonic() < deadline:
            message = pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0,
            )
        assert message is not None, "Redis never published the committed update"
        assert message["type"] == "message"
        payload = json.loads(message["data"])
        assert payload["kind"] == "score_changed"
        assert payload["match_id"] == match_id
        assert payload["data"]["home_score"] == 1

        with TestClient(app) as client:
            assert client.get("/ready").json() == {
                "status": "ready", "database": "connected",
            }
            snapshot = client.get(f"/api/v1/matches/{match_id}").json()
            assert snapshot["home_score"] == 1
            updates = client.get(f"/api/v1/matches/{match_id}/updates").json()
            assert any(item["id"] == payload["id"] for item in updates)
            assert client.get(
                f"/api/v1/matches/{match_id}/updates",
                params={"after_id": payload["id"]},
            ).json() == []
        with SessionLocal() as db:
            assert drain_outbox(db, redis) == 0
    finally:
        pubsub.close()
        redis.close()


def test_postgres_transaction_rollback_preserves_stream_consistency() -> None:
    from app.models import LiveUpdate
    from app.services.live_updates import record_update

    fixture = normalize_fixture({
        "fixture": {"id": 990124, "date": "2026-11-17T19:00:00Z",
                    "status": {"short": "1H"}},
        "league": {"id": 990, "name": "CI Integration League"},
        "teams": {"home": {"id": 9901, "name": "CI Home"},
                  "away": {"id": 9902, "name": "CI Away"}},
        "goals": {"home": 0, "away": 0},
    })
    with SessionLocal.begin() as db:
        upsert_fixtures(db, [fixture])
        db.flush()
        match = db.scalar(select(Match).where(Match.provider_id == "990124"))
        match_id = match.id
        before = db.scalar(select(func.count()).select_from(
            LiveUpdate
        ).where(LiveUpdate.match_id == match_id))
    try:
        with SessionLocal.begin() as db:
            record_update(db, match_id, "test_rolled_back", {"unexpected": True})
            db.flush()
            raise ValueError("Simulated ingestion failure")
    except ValueError:
        pass
    with SessionLocal() as db:
        after = db.scalar(select(func.count()).select_from(
            LiveUpdate
        ).where(LiveUpdate.match_id == match_id))
    assert after == before
