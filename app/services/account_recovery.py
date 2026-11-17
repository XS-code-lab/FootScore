"""One-time token lifecycle and best-effort SMTP SSL delivery.

Only token *hashes* are stored in the database, and raw codes are never
logged, saved in a queue, or returned to anonymous API clients.
"""
import hashlib
import hmac
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuthOneTimeToken, User
from app.security import _jwt_secret, hash_password
from app.settings import settings

TTL_MINUTES = {"verify_email": 60, "reset_password": 15}


def email_is_configured() -> bool:
    return all((
        settings.smtp_host,
        settings.smtp_sender,
        settings.smtp_username,
        settings.smtp_password,
    ))


def _token_hash(token: str) -> str:
    # HMAC also protects if the database is exposed; JWT_SECRET must stay private.
    return hmac.new(
        _jwt_secret().encode("utf-8"),
        token.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def issue_code(db: Session, user: User, purpose: str) -> str:
    """Replace prior unconsumed codes, return only to trusted email sender."""
    if purpose not in TTL_MINUTES:
        raise ValueError("Unknown recovery purpose")
    now = datetime.now(timezone.utc)
    db.execute(
        update(AuthOneTimeToken)
        .where(
            AuthOneTimeToken.user_id == user.id,
            AuthOneTimeToken.purpose == purpose,
            AuthOneTimeToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    code = secrets.token_urlsafe(32)
    db.add(AuthOneTimeToken(
        user_id=user.id, purpose=purpose,
        token_hash=_token_hash(code), created_at=now,
        expires_at=now + timedelta(minutes=TTL_MINUTES[purpose]),
    ))
    return code


def _consume(db: Session, token: str, purpose: str) -> User:
    if purpose not in TTL_MINUTES or not 32 <= len(token) <= 128:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    # Database lock serializes consumption in PostgreSQL; only one use succeeds.
    row = db.scalar(select(AuthOneTimeToken).where(
        AuthOneTimeToken.token_hash == _token_hash(token),
        AuthOneTimeToken.purpose == purpose,
    ).with_for_update())
    now = datetime.now(timezone.utc)
    if row is None or row.consumed_at is not None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)  # SQLite dev/test
    if expires <= now:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    row.consumed_at = now
    return user


def verify_email(db: Session, token: str) -> None:
    user = _consume(db, token, "verify_email")
    user.email_verified_at = datetime.now(timezone.utc)
    db.commit()


def reset_password(db: Session, token: str, new_password: str) -> None:
    if not 12 <= len(new_password) <= 128:
        raise HTTPException(status_code=422, detail="Invalid password length")
    user = _consume(db, token, "reset_password")
    # Database-side increment avoids lost session revocations when a
    # concurrent authenticated change/logout updates this same account.
    db.execute(update(User).where(User.id == user.id).values(
        password_hash=hash_password(new_password),
        session_version=User.session_version + 1,
    ))
    db.execute(
        update(AuthOneTimeToken)
        .where(
            AuthOneTimeToken.user_id == user.id,
            AuthOneTimeToken.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(timezone.utc))
    )
    db.commit()


def send_email_code(address: str, token: str, purpose: str) -> None:
    """Send over implicit TLS (SMTPS); never print or return the token."""
    if not email_is_configured():
        raise RuntimeError("SMTP delivery is not configured")
    message = EmailMessage()
    message["From"] = settings.smtp_sender
    message["To"] = address
    subject = (
        "FootScore email confirmation" if purpose == "verify_email"
        else "FootScore password reset"
    )
    message["Subject"] = subject
    message.set_content(
        "FootScore security code:\n\n"
        + token + "\n\n"
        + f"Expires in {TTL_MINUTES[purpose]} minutes. "
        + "If you did not request this, ignore this email.\n"
    )
    with smtplib.SMTP_SSL(
        settings.smtp_host, settings.smtp_port,
        context=ssl.create_default_context(), timeout=10,
    ) as client:
        client.login(
            settings.smtp_username, settings.smtp_password.get_secret_value(),
        )
        client.send_message(message)


def request_code(db: Session, address: str, purpose: str) -> None:
    """Anonymous response must not reveal account existence or mail failures."""
    if not email_is_configured():
        return
    user = db.scalar(select(User).where(User.email == address))
    if user is None or not user.is_active:
        return
    if purpose == "verify_email" and user.email_verified_at is not None:
        return
    token = issue_code(db, user, purpose)
    db.commit()
    try:
        send_email_code(user.email, token, purpose)
    except (OSError, smtplib.SMTPException):
        # A valid token can be retried only by generating a fresh new request.
        # Do not expose SMTP errors or raw tokens to public clients.
        pass
