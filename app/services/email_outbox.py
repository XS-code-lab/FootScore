"""Transactional, encrypted queue for one-time security emails.

PostgreSQL SKIP LOCKED supports competing senders. We cannot atomically
commit SMTP acceptance with a database update; delivery is *at least once*.
Never log decrypted codes, email bodies, or SMTP exception descriptions.
"""
import smtplib
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthOneTimeToken, EmailDelivery, User
from app.settings import settings

MAX_ATTEMPTS = 5
MAX_BATCH = 25


def _fernet() -> Fernet:
    raw = settings.email_queue_key
    if not raw:
        raise RuntimeError("EMAIL_QUEUE_KEY must be configured for secure email queuing")
    try:
        return Fernet(raw.get_secret_value().encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid EMAIL_QUEUE_KEY") from exc


def encrypt_code(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def _valid_until(value: datetime) -> datetime:
    """SQLite development can read timezone-aware values as naive UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _discard(row: EmailDelivery, status: str, reason: str | None = None) -> None:
    row.status = status
    row.encrypted_code = None
    row.failure_reason = reason


def drain_email_outbox(
    db: Session, *, limit: int = 10, now: datetime | None = None,
) -> dict:
    """Send a bounded batch; commit is controlled by the caller.

    The SMTP call happens while selected DB rows remain locked. Rejected
    addresses and expired/used one-time codes are not emailed.
    """
    if not 1 <= limit <= MAX_BATCH:
        raise ValueError("Email batch limit must be 1..25")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Email worker clock must be timezone-aware")
    if not settings.email_queue_key:
        raise RuntimeError("EMAIL_QUEUE_KEY is not configured")
    from app.services.account_recovery import email_is_configured, send_email_code

    if not email_is_configured():
        # Keep encrypted pending items until the mail service is configured.
        return {"sent": 0, "retried": 0, "expired": 0, "failed": 0}

    query = (
        select(EmailDelivery)
        .where(
            EmailDelivery.status == "pending",
            EmailDelivery.next_attempt_at <= now,
        )
        .order_by(EmailDelivery.id)
        .limit(limit)
    )
    if db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = db.scalars(query).all()
    results = {"sent": 0, "retried": 0, "expired": 0, "failed": 0}
    cipher = _fernet()
    for row in rows:
        token = db.get(AuthOneTimeToken, row.token_id)
        user = db.get(User, token.user_id) if token else None
        if (
            token is None or user is None or not user.is_active
            or token.consumed_at is not None
            or _valid_until(token.expires_at) <= now
            or (token.purpose == "verify_email" and user.email_verified_at is not None)
        ):
            _discard(row, "expired")
            results["expired"] += 1
            continue
        try:
            plaintext = cipher.decrypt(row.encrypted_code.encode("ascii")).decode("utf-8")
        except (InvalidToken, AttributeError, UnicodeError, ValueError):
            # Wrong encryption key: never send arbitrary text.
            # Keep the ciphertext for operator recovery; stop automatic sends.
            row.status = "failed"
            row.failure_reason = "decrypt_failed"
            results["failed"] += 1
            continue
        try:
            send_email_code(user.email, plaintext, token.purpose)
        except (smtplib.SMTPException, OSError):
            row.attempts += 1
            if row.attempts >= MAX_ATTEMPTS:
                _discard(row, "failed", "smtp_retry_exhausted")
                results["failed"] += 1
            else:
                # Safe bounded retry, never more than one attempt per drain.
                row.next_attempt_at = now + timedelta(
                    seconds=min(900, 60 * 2 ** (row.attempts - 1)),
                )
                row.failure_reason = "smtp_temporary_failure"
                results["retried"] += 1
            continue
        row.attempts += 1
        row.sent_at = datetime.now(timezone.utc)
        _discard(row, "sent")
        results["sent"] += 1
    db.flush()
    return results
