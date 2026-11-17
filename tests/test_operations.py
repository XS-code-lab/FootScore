"""Read-only operator checks and transactional Redis failure recovery."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    AuthOneTimeToken, EmailDelivery, LiveUpdate, Match,
    ProviderQuota, ProviderRequestLog, User,
)
from app.outbox_cli import publish_once, retry_delay
from app.services.live_updates import record_update
from app.services.operations import assess_snapshot, snapshot
from app.settings import settings


def _engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def test_empty_monitor_is_read_only_and_leaks_no_private_data() -> None:
    engine = _engine()
    with Session(engine) as db:
        result = snapshot(db, now=datetime(2026, 11, 17, tzinfo=timezone.utc))
        assert result["match_outbox"] == {
            "unpublished": 0, "oldest_unpublished_age_seconds": None,
        }
        assert result["security_email_outbox"] == {
            "pending": 0, "due_now": 0,
            "failed": 0, "oldest_pending_age_seconds": None,
        }
        assert result["redis_reachable"] is None
        assert result["smtp_deliverability_verified"] is False
        assert result["api_football_local_quota"]["provider_account_usage_verified"] is False
        assert assess_snapshot(result)["state"] == "ok"
        assert db.scalar(select(func.count()).select_from(LiveUpdate)) == 0
    engine.dispose()


def test_monitor_flags_backlog_and_failed_mail_without_identifiers(monkeypatch) -> None:
    engine = _engine()
    monkeypatch.setattr(settings, "provider_daily_limit", 100)
    monkeypatch.setattr(settings, "provider_reserve", 10)
    monkeypatch.setattr(settings, "provider_per_minute_limit", 8)
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        match = Match(
            provider="api-football", provider_id="monitor-only",
            status="1H",
        )
        user = User(
            email="private-person@example.test", password_hash="not-real",
        )
        db.add_all([match, user])
        db.flush()
        db.add(LiveUpdate(
            match_id=match.id, kind="score_changed",
            payload={"private_internal_test": "do-not-expose"},
            created_at=now - timedelta(seconds=181),
        ))
        db.add(LiveUpdate(
            match_id=match.id, kind="score_changed",
            payload={"score": 0}, created_at=now - timedelta(seconds=5),
        ))
        db.add(AuthOneTimeToken(
            user_id=user.id, token_hash="a" * 64,
            purpose="reset_password",
            created_at=now - timedelta(minutes=8),
            expires_at=now + timedelta(minutes=7),
        ))
        db.flush()
        token_id = db.scalar(select(AuthOneTimeToken.id))
        db.add_all([
            EmailDelivery(
                token_id=token_id, encrypted_code="secret-ciphertext",
                status="pending", attempts=1,
                created_at=now - timedelta(seconds=401),
                next_attempt_at=now - timedelta(seconds=10),
            ),
        ])
        db.add(ProviderQuota(
            provider="api-football", day=now.date(), used=6,
        ))
        db.add_all([
            ProviderRequestLog(
                provider="api-football", reserved_at=now - timedelta(seconds=20),
            ),
            ProviderRequestLog(
                provider="api-football", reserved_at=now - timedelta(seconds=80),
            ),
        ])
        db.commit()
        result = snapshot(db, now=now)
        assert result["match_outbox"] == {
            "unpublished": 2, "oldest_unpublished_age_seconds": 181,
        }
        assert result["security_email_outbox"] == {
            "pending": 1, "due_now": 1,
            "failed": 0, "oldest_pending_age_seconds": 401,
        }
        assert result["api_football_local_quota"] == {
            "used_today": 6, "reserved_last_60_seconds": 1,
            "daily_available_after_reserve": 84,
            "minute_available": 7,
            "provider_account_usage_verified": False,
        }
        alerts = assess_snapshot(result, max_match_age=120, max_email_age=300)
        assert alerts["alerts"] == [
            "match_outbox_delayed", "security_email_delivery_delayed",
        ]
        raw = json.dumps(result)
        assert "private-person@example.test" not in raw
        assert "secret-ciphertext" not in raw
        assert "do-not-expose" not in raw
        assert "a" * 64 not in raw
        assert db.scalar(select(func.count()).select_from(EmailDelivery)) == 1
    engine.dispose()


def test_alert_thresholds_fails_closed_for_invalid_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        assess_snapshot({"match_outbox": {}, "security_email_outbox": {}},
                        max_match_age=0)
    with pytest.raises(ValueError, match="positive"):
        assess_snapshot({"match_outbox": {}, "security_email_outbox": {}},
                        max_email_age=-1)
    with pytest.raises(ValueError, match="timezone-aware"):
        snapshot(Session(_engine()), now=datetime(2026, 11, 17))
    assert [retry_delay(n) for n in [1, 2, 3, 4, 5, 6, 20]] == [
        1, 2, 4, 8, 16, 30, 30,
    ]
    assert retry_delay(10, cap_seconds=5) == 5
    with pytest.raises(ValueError, match="Invalid"):
        retry_delay(0)


def test_redis_failure_rolls_back_batch_then_replays_at_least_once() -> None:
    engine = _engine()
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        match = Match(provider="api-football", provider_id="redis-fail")
        db.add(match)
        db.flush()
        match_id = match.id
        record_update(db, match_id, "goal", {"home": 1})
        record_update(db, match_id, "score", {"home": 1})

    class FailingRedis:
        def __init__(self):
            self.calls = []
        def publish(self, channel, payload):
            self.calls.append((channel, json.loads(payload)))
            if len(self.calls) == 2:
                raise RedisConnectionError("simulated Redis outage")
            return 1

    broken = FailingRedis()
    with pytest.raises(RedisConnectionError):
        publish_once(broken, session_factory=factory)
    assert len(broken.calls) == 2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(
            LiveUpdate
        ).where(LiveUpdate.published_at.is_(None))) == 2

    class WorkingRedis:
        def __init__(self):
            self.calls = []
        def publish(self, channel, payload):
            self.calls.append((channel, json.loads(payload)))
            return 1

    working = WorkingRedis()
    assert publish_once(working, session_factory=factory) == 2
    assert publish_once(working, session_factory=factory) == 0
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(
            LiveUpdate
        ).where(LiveUpdate.published_at.is_(None))) == 0
    assert len(working.calls) == 2
    assert broken.calls[0][1]["id"] == working.calls[0][1]["id"]
    assert len({payload["id"] for _, payload in working.calls}) == 2
    engine.dispose()
