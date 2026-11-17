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
        "historical_player_fetches", "league_coverage_probes", "auth_rate_buckets",
        "auth_one_time_tokens",
    } <= tables
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0009_account_recovery"

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


def test_multiple_publishers_claim_distinct_postgres_outbox_rows() -> None:
    """Two workers must not publish the same claimed outbox row."""
    from app.models import LiveUpdate
    from app.services.live_updates import record_update

    assert settings.redis_url
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        with SessionLocal.begin() as db:
            # Clear any unrelated pending updates left by other test cases.
            drain_outbox(db, redis)
        with SessionLocal.begin() as db:
            fixture = normalize_fixture({
                "fixture": {"id": 991007, "date": "2026-11-17T20:00:00Z",
                            "status": {"short": "1H"}},
                "league": {"id": 991, "name": "CI Parallel League"},
                "teams": {"home": {"id": 9911, "name": "Home"},
                          "away": {"id": 9912, "name": "Away"}},
                "goals": {"home": 0, "away": 0},
            })
            upsert_fixtures(db, [fixture])
            db.flush()
            match_id = db.scalar(select(Match.id).where(
                Match.provider_id == "991007"
            ))
            record_update(db, match_id, "parallel_test", {"sequence": 1})
            # The fixture score update may also be pending. Count all rows.
        with SessionLocal() as db:
            expected = db.scalars(select(LiveUpdate.id).where(
                LiveUpdate.published_at.is_(None),
            ).order_by(LiveUpdate.id)).all()
        assert len(expected) == 2
        gate = Barrier(2)

        def publish_one() -> int:
            gate.wait(timeout=10)
            with SessionLocal.begin() as db:
                return drain_outbox(db, redis, limit=1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(lambda _: publish_one(), range(2)))
        assert counts == [1, 1]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                LiveUpdate
            ).where(LiveUpdate.published_at.is_(None))) == 0
    finally:
        redis.close()


def test_parallel_auth_attempts_do_not_bypass_login_budget(monkeypatch) -> None:
    from pydantic import SecretStr
    from starlette.requests import Request

    from app.models import AuthRateBucket
    from app.services.auth_rate import check_auth_limit

    monkeypatch.setattr(settings, "jwt_secret", SecretStr("integration-auth-key-" * 3))
    workers = 16
    gate = Barrier(workers)

    def attempt() -> int:
        scope = {
            "type": "http", "client": ("203.0.113.41", 1234),
            "headers": [], "method": "POST", "path": "/api/v1/auth/login",
            "scheme": "http", "server": ("localhost", 80), "query_string": b"",
        }
        gate.wait(timeout=10)
        try:
            with SessionLocal() as db:
                return check_auth_limit(db, Request(scope), "login")
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException) and exc.status_code == 429:
                return 0
            raise

    with ThreadPoolExecutor(max_workers=workers) as pool:
        values = list(pool.map(lambda _: attempt(), range(workers)))
    assert sorted(x for x in values if x) == list(range(1, 11))
    assert values.count(0) == 6
    with SessionLocal() as db:
        buckets = db.scalars(select(AuthRateBucket)).all()
        assert len(buckets) == 1
        assert buckets[0].used == 10
