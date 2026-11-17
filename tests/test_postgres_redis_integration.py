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
        "auth_one_time_tokens", "email_deliveries",
    } <= tables
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0010_email_delivery_outbox"

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


def test_two_parallel_password_resets_accept_one_token_only(monkeypatch) -> None:
    """PostgreSQL row lock prevents two consumers accepting the same code."""
    from fastapi import HTTPException
    from pydantic import SecretStr
    from app.models import AuthOneTimeToken, User
    from app.services.account_recovery import issue_code, reset_password
    from app.security import hash_password

    monkeypatch.setattr(settings, "jwt_secret", SecretStr("ci-account-recovery-" * 3))
    with SessionLocal.begin() as db:
        user = User(email="reset-concurrent@example.com",
                    password_hash=hash_password("OriginalPass123!"))
        db.add(user)
        db.flush()
        user_id = user.id
        code = issue_code(db, user, "reset_password")

    gate = Barrier(2)

    def consume() -> bool:
        gate.wait(timeout=10)
        try:
            with SessionLocal() as db:
                reset_password(db, code, "NewSecurePassword123!")
            return True
        except HTTPException as exc:
            assert exc.status_code == 400
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sorted(results) == [False, True]
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user.session_version == 1
        token = db.scalar(select(AuthOneTimeToken).where(
            AuthOneTimeToken.user_id == user_id,
        ))
        assert token.consumed_at is not None


def test_concurrent_all_sessions_revocations_are_compare_and_swap(monkeypatch) -> None:
    """Two concurrent requests with one stale JWT version cannot both succeed."""
    from fastapi import HTTPException
    from pydantic import SecretStr

    from app.models import User
    from app.security import hash_password
    from app.services.session_security import revoke_all_sessions

    monkeypatch.setattr(settings, "jwt_secret", SecretStr("ci-session-revoke-" * 3))
    with SessionLocal.begin() as db:
        user = User(
            email="concurrent-revoke@example.com",
            password_hash=hash_password("SharedPassword123!"),
        )
        db.add(user)
        db.flush()
        user_id = user.id
        old_version = user.session_version

    gate = Barrier(2)

    def revoke() -> bool:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            gate.wait(timeout=10)
            try:
                revoke_all_sessions(
                    db, user, "SharedPassword123!",
                    expected_version=old_version,
                )
                return True
            except HTTPException as exc:
                assert exc.status_code == 401
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: revoke(), range(2)))
    assert sorted(results) == [False, True]
    with SessionLocal() as db:
        assert db.get(User, user_id).session_version == old_version + 1


def test_parallel_postgres_email_workers_send_each_code_once(monkeypatch) -> None:
    """Two email workers claim distinct encrypted messages via SKIP LOCKED."""
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.models import AuthOneTimeToken, EmailDelivery, User
    from app.services import account_recovery
    from app.services.email_outbox import drain_email_outbox

    monkeypatch.setattr(settings, "jwt_secret", SecretStr("ci-email-code-test-" * 3))
    monkeypatch.setattr(
        settings, "email_queue_key", SecretStr(Fernet.generate_key().decode()),
    )
    monkeypatch.setattr(settings, "smtp_host", "smtp.mock.example")
    monkeypatch.setattr(settings, "smtp_username", "ci")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("ci-mock-only"))
    monkeypatch.setattr(settings, "smtp_sender", "ci@example.test")
    sent = []
    monkeypatch.setattr(
        account_recovery, "send_email_code",
        lambda address, code, purpose: sent.append((address, code, purpose)),
    )
    for n in range(2):
        email = f"email-worker-{n}@example.test"
        with SessionLocal.begin() as db:
            db.add(User(email=email, password_hash="ci-not-a-real-password"))
        with SessionLocal() as db:
            account_recovery.request_code(db, email, "verify_email")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(
            EmailDelivery
        ).where(EmailDelivery.status == "pending")) == 2
    gate = Barrier(2)

    def publish_one() -> dict:
        gate.wait(timeout=10)
        with SessionLocal.begin() as db:
            return drain_email_outbox(db, limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish_one(), range(2)))
    assert [r["sent"] for r in results] == [1, 1]
    assert len(sent) == 2
    assert {row[0] for row in sent} == {
        "email-worker-0@example.test", "email-worker-1@example.test",
    }
    with SessionLocal() as db:
        deliveries = db.scalars(select(EmailDelivery)).all()
        assert len(deliveries) == 2
        assert all(row.status == "sent" and row.encrypted_code is None
                   for row in deliveries)
        assert db.scalar(select(func.count()).select_from(
            AuthOneTimeToken
        ).where(AuthOneTimeToken.consumed_at.is_(None))) == 2
