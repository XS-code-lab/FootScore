"""Encrypted security-email queue: durable inserts, retries and stale-code safety."""
import smtplib
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AuthOneTimeToken, EmailDelivery, User
from app.services import account_recovery
from app.services.email_outbox import drain_email_outbox
from app.services.maintenance import cleanup_auth_state
from app.settings import settings


def _setup(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "jwt_secret", SecretStr("mail-signed-secret-" * 3))
    monkeypatch.setattr(
        settings, "email_queue_key", SecretStr(Fernet.generate_key().decode()),
    )
    monkeypatch.setattr(settings, "smtp_host", "mail.example.test")
    monkeypatch.setattr(settings, "smtp_sender", "noreply@example.test")
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("test-smtp-only"))
    delivered = []
    monkeypatch.setattr(
        account_recovery, "send_email_code",
        lambda address, code, purpose: delivered.append((address, code, purpose)),
    )
    with Session(engine) as db:
        db.add(User(
            email="pilot@example.test", password_hash="test-only",
        ))
        db.commit()
    return engine, delivered


def _request(db: Session, purpose: str = "verify_email") -> None:
    user = db.scalar(select(User))
    account_recovery.request_code(db, user.email, purpose)


def test_code_is_queued_encrypted_and_only_sent_by_worker(monkeypatch):
    engine, delivered = _setup(monkeypatch)
    with Session(engine) as db:
        _request(db)
        assert delivered == []
        row = db.scalar(select(EmailDelivery))
        token = db.scalar(select(AuthOneTimeToken))
        assert row.status == "pending"
        assert row.attempts == 0
        assert row.encrypted_code != token.token_hash
        assert token.token_hash not in row.encrypted_code
        original_ciphertext = row.encrypted_code
        assert original_ciphertext.startswith("gAAAA")
        assert db.scalar(select(func.count()).select_from(EmailDelivery)) == 1
        summary = drain_email_outbox(db)
        db.commit()
        assert summary == {"sent": 1, "retried": 0, "expired": 0, "failed": 0}
        assert len(delivered) == 1
        address, clear_code, purpose = delivered[0]
        assert address == "pilot@example.test"
        assert purpose == "verify_email"
        assert clear_code not in original_ciphertext
        assert account_recovery._token_hash(clear_code) == token.token_hash
        row = db.scalar(select(EmailDelivery))
        assert row.status == "sent"
        assert row.encrypted_code is None
        assert row.sent_at is not None
        assert drain_email_outbox(db)["sent"] == 0
        account_recovery.verify_email(db, clear_code)
        assert db.scalar(select(User.email_verified_at)) is not None
    engine.dispose()


def test_replacing_code_cancels_old_pending_mail(monkeypatch):
    engine, delivered = _setup(monkeypatch)
    with Session(engine) as db:
        _request(db)
        _request(db)  # invalidate first code and insert another encrypted message
        assert db.scalar(select(func.count()).select_from(EmailDelivery)) == 2
        outcome = drain_email_outbox(db)
        db.commit()
        assert outcome["expired"] == 1
        assert outcome["sent"] == 1
        assert len(delivered) == 1
        old, new = db.scalars(
            select(EmailDelivery).order_by(EmailDelivery.id)
        ).all()
        assert old.status == "expired" and old.encrypted_code is None
        assert new.status == "sent"
    engine.dispose()


def test_smtp_retry_backoff_then_expiration(monkeypatch):
    engine, _ = _setup(monkeypatch)
    def fail_send(*args):
        raise smtplib.SMTPServerDisconnected("credentials and code not logged")

    monkeypatch.setattr(account_recovery, "send_email_code", fail_send)
    with Session(engine) as db:
        _request(db, "reset_password")
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        first = drain_email_outbox(db, now=now)
        db.commit()
        assert first["retried"] == 1 and first["sent"] == 0
        row = db.scalar(select(EmailDelivery))
        assert row.status == "pending" and row.attempts == 1
        assert row.failure_reason == "smtp_temporary_failure"
        scheduled = row.next_attempt_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        assert scheduled > now
        assert drain_email_outbox(db, now=now + timedelta(seconds=1)) == {
            "sent": 0, "retried": 0, "expired": 0, "failed": 0,
        }
        second = drain_email_outbox(db, now=now + timedelta(seconds=70))
        db.commit()
        assert second["retried"] == 1
        assert row.attempts == 2
        # A reset code is never sent after its fifteen-minute expiry.
        expired = drain_email_outbox(db, now=now + timedelta(minutes=16))
        db.commit()
        assert expired["expired"] == 1
        assert row.status == "expired" and row.encrypted_code is None
    engine.dispose()


def test_retry_exhaustion_and_decrypt_key_mismatch(monkeypatch):
    engine, _ = _setup(monkeypatch)
    def fail_send(*args):
        raise OSError("SMTP unavailable")

    monkeypatch.setattr(account_recovery, "send_email_code", fail_send)
    with Session(engine) as db:
        _request(db)
        start = datetime.now(timezone.utc) + timedelta(seconds=1)
        times = (0, 70, 200, 450, 1000)
        for step, offset in enumerate(times, start=1):
            outcome = drain_email_outbox(db, now=start + timedelta(seconds=offset))
            db.commit()
            if step < 5:
                assert outcome["retried"] == 1
            else:
                assert outcome["failed"] == 1
        row = db.scalar(select(EmailDelivery))
        assert row.status == "failed"
        assert row.attempts == 5
        assert row.encrypted_code is None
        _request(db, "reset_password")
        other = db.scalars(
            select(EmailDelivery).order_by(EmailDelivery.id.desc())
        ).first()
        ciphertext = other.encrypted_code
        monkeypatch.setattr(
            settings, "email_queue_key", SecretStr(Fernet.generate_key().decode()),
        )
        outcome = drain_email_outbox(db)
        db.commit()
        assert outcome["failed"] == 1
        assert other.status == "failed" and other.failure_reason == "decrypt_failed"
        assert other.encrypted_code == ciphertext  # preserve for operator recovery
    engine.dispose()


def test_smtp_missing_keeps_pending_and_cleanup_removes_encrypted_row(monkeypatch):
    engine, _ = _setup(monkeypatch)
    with Session(engine) as db:
        _request(db)
        monkeypatch.setattr(settings, "smtp_host", None)
        assert drain_email_outbox(db)["sent"] == 0
        row = db.scalar(select(EmailDelivery))
        assert row.status == "pending" and row.encrypted_code
        token = db.scalar(select(AuthOneTimeToken))
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        assert cleanup_auth_state(db)["expired_tokens_selected"] == 1
        cleanup_auth_state(db, execute=True)
        assert db.scalar(select(func.count()).select_from(EmailDelivery)) == 0
        assert db.scalar(select(func.count()).select_from(AuthOneTimeToken)) == 0
    engine.dispose()


def test_invalid_batch_is_rejected(monkeypatch):
    engine, _ = _setup(monkeypatch)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="1..25"):
            drain_email_outbox(db, limit=26)
    engine.dispose()
