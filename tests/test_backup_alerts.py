"""Safe, deduplicated backup-health alerts without external network calls."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.backup_alert_cli import check_once
from app.db import Base
from app.models import BackupAlertState
from app.services.backup_alerts import (
    deliver_backup_alert, proposed_event, scope_digest,
    send_health_webhook, validate_webhook_url,
)

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
BUCKET = "footscore-test-bucket"
PREFIX = "footscore"


def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def transition(session_factory, state, sent, at=NOW, remind_hours=24):
    with session_factory.begin() as db:
        return deliver_backup_alert(
            db, bucket=BUCKET, prefix=PREFIX, condition=state,
            sender=lambda payload: sent.append(payload), at=at,
            remind_hours=remind_hours,
        )


def test_state_changes_reminders_and_recovery_are_deduplicated():
    engine, sessions = factory()
    sent = []
    initial = transition(sessions, "recent_unverified", sent)
    assert initial["event"] is None and not sent
    first = transition(sessions, "missing", sent)
    assert first["webhook_sent"] and first["event"] == "backup_missing"
    assert not transition(sessions, "missing", sent, at=NOW+timedelta(hours=1))[
        "webhook_sent"
    ]
    assert transition(
        sessions, "missing", sent, at=NOW+timedelta(hours=24),
    )["event"] == "backup_missing"
    assert transition(
        sessions, "stale", sent, at=NOW+timedelta(hours=25),
    )["event"] == "backup_stale"
    assert transition(
        sessions, "error", sent, at=NOW+timedelta(hours=26),
    )["event"] == "backup_inventory_unavailable"
    assert transition(
        sessions, "recent_unverified", sent, at=NOW+timedelta(hours=27),
    )["event"] == "backup_visibility_restored"
    assert not transition(
        sessions, "recent_unverified", sent, at=NOW+timedelta(hours=28),
    )["webhook_sent"]
    assert [x["event"] for x in sent] == [
        "backup_missing", "backup_missing", "backup_stale",
        "backup_inventory_unavailable", "backup_visibility_restored",
    ]
    assert all(x["service"] == "FootScore" for x in sent)
    assert all(not x["cryptographic_integrity_verified"] for x in sent)
    assert all(not x["restore_verified"] for x in sent)
    assert all("bucket" not in json.dumps(x).lower() for x in sent)
    with sessions() as db:
        rows = db.scalars(select(BackupAlertState)).all()
        assert len(rows) == 1
        assert rows[0].scope_hash == scope_digest(BUCKET, PREFIX)
        assert BUCKET not in rows[0].scope_hash
        assert rows[0].condition == "recent_unverified"
    engine.dispose()


def test_webhook_failure_rolls_back_state_and_retries():
    engine, sessions = factory()
    attempts = []
    def failing(payload):
        attempts.append(payload)
        raise RuntimeError("test-only unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        with sessions.begin() as db:
            deliver_backup_alert(
                db, bucket=BUCKET, prefix=PREFIX,
                condition="missing", sender=failing, at=NOW,
            )
    with sessions() as db:
        assert db.scalar(select(BackupAlertState)) is None
    sent = []
    again = transition(sessions, "missing", sent)
    assert again["event"] == "backup_missing"
    assert len(attempts) == len(sent) == 1
    engine.dispose()


def test_webhook_failure_keeps_previous_condition():
    engine, sessions = factory()
    sent = []
    transition(sessions, "missing", sent)
    with pytest.raises(RuntimeError):
        with sessions.begin() as db:
            deliver_backup_alert(
                db, bucket=BUCKET, prefix=PREFIX,
                condition="recent_unverified",
                sender=lambda payload: (_ for _ in ()).throw(RuntimeError()),
                at=NOW+timedelta(hours=1),
            )
    with sessions() as db:
        assert db.scalar(select(BackupAlertState.condition)) == "missing"
    assert transition(
        sessions, "recent_unverified", sent,
        at=NOW+timedelta(hours=2),
    )["event"] == "backup_visibility_restored"
    engine.dispose()


def test_webhook_http_payload_and_non_success_are_sanitized():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.scheme == "https"
        data = json.loads(request.content)
        assert data["event"] == "backup_stale"
        assert "object_key" not in data
        return httpx.Response(204)

    msg = {"service": "FootScore", "event": "backup_stale"}
    send_health_webhook(
        msg, url="https://hooks.example.test/backup",
        transport=httpx.MockTransport(handler),
    )
    assert len(requests) == 1
    with pytest.raises(RuntimeError, match="non-success"):
        send_health_webhook(
            msg, url="https://hooks.example.test/backup",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, text="SECRET_DO_NOT_PRINT"),
            ),
        )


@pytest.mark.parametrize("url", [
    "", "http://hooks.example.test/path", "https://localhost/test",
    "https://127.0.0.1/test", "https://[::1]/test",
    "https://hooks.example.test:8443/test",
    "https://user:pass@hooks.example.test/test",
    "https://hooks.example.test/path#fragment",
    "https://internal.local/test", "file:///tmp/test",
])
def test_invalid_or_private_webhooks_rejected(url):
    with pytest.raises(ValueError):
        validate_webhook_url(url)


@pytest.mark.parametrize("hours", [0, 169])
def test_invalid_reminder_does_not_send(hours):
    with pytest.raises(ValueError):
        proposed_event(
            "unknown", "missing", notified_at=None, now=NOW, remind_hours=hours,
        )


def test_monitor_dry_run_never_touches_database_or_webhook():
    engine, sessions = factory()
    sent = []
    class UnavailableS3:
        def list_objects_v2(self, **kwargs):
            raise OSError("synthetic listing unavailable")
    res = check_once(
        UnavailableS3(), bucket=BUCKET, session_factory=sessions,
        sender=lambda payload: sent.append(payload),
    )
    assert res == {
        "dry_run": True, "state": "error", "listing_failed": True,
        "webhook_sent": False, "database_modified": False,
    }
    with sessions() as db:
        assert db.scalar(select(BackupAlertState)) is None
    assert sent == []
    engine.dispose()


def test_listing_failure_is_not_misclassified_healthy_and_recovers():
    engine, sessions = factory()
    sent = []
    class DynamicS3:
        available = False
        def list_objects_v2(self, **kwargs):
            if not self.available:
                raise OSError("test-only connection failure")
            return {"Contents": [], "IsTruncated": False}
    fake = DynamicS3()
    alert = check_once(
        fake, bucket=BUCKET, session_factory=sessions, execute=True,
        sender=lambda payload: sent.append(payload),
    )
    assert alert["listing_failed"]
    assert alert["event"] == "backup_inventory_unavailable"
    assert not check_once(
        fake, bucket=BUCKET, session_factory=sessions, execute=True,
        sender=lambda payload: sent.append(payload),
    )["webhook_sent"]
    fake.available = True
    missing = check_once(
        fake, bucket=BUCKET, session_factory=sessions, execute=True,
        sender=lambda payload: sent.append(payload),
    )
    assert missing["event"] == "backup_missing"
    assert len(sent) == 2
    engine.dispose()
