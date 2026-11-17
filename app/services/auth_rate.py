"""Database-coordinated per-peer limits on login and registration.

The peer is request.client.host, *not* an untrusted X-Forwarded-For header.
Deploy with a deliberately configured trusted reverse-proxy IP strategy;
otherwise all NAT/proxy users share a peer bucket (safe but inconvenient).
"""
import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import AuthRateBucket
from app.settings import settings

WINDOW_SECONDS = 900
LIMITS = {
    "login": 10, "register": 5, "verify": 3, "reset": 3,
    "password_change": 5, "revoke_all": 5,
}


def _peer_key(request: Request, action: str, now: datetime) -> str:
    secret = settings.jwt_secret.get_secret_value() if settings.jwt_secret else ""
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    peer = request.client.host if request.client else "unknown"
    # The raw IP address is never stored; the HMAC rotates with JWT_SECRET.
    digest = hmac.new(
        secret.encode(), f"{action}:{peer}".encode(), hashlib.sha256,
    ).hexdigest()
    window = int(now.timestamp()) // WINDOW_SECONDS
    return f"{action}:{window}:{digest}"


def check_auth_limit(
    db: Session, request: Request, action: str, *,
    now: datetime | None = None,
) -> int:
    """Charge one attempt before hashing a password or inserting a user.

    PostgreSQL serializes contenders by locking the bucket row. SQLite
    supports local development only. Even rejected credentials cost a slot.
    """
    if action not in LIMITS:
        raise ValueError("Unknown auth action")
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timestamp must be timezone aware")
    now = now.astimezone(timezone.utc)
    key = _peer_key(request, action, now)
    expires = datetime.fromtimestamp(
        (int(now.timestamp()) // WINDOW_SECONDS + 1) * WINDOW_SECONDS,
        timezone.utc,
    )
    dialect = db.bind.dialect.name
    values = {"key": key, "used": 0, "expires_at": expires}
    if dialect == "postgresql":
        stmt = pg_insert(AuthRateBucket).values(**values).on_conflict_do_nothing(
            index_elements=["key"]
        )
    elif dialect == "sqlite":
        stmt = sqlite_insert(AuthRateBucket).values(**values).on_conflict_do_nothing(
            index_elements=["key"]
        )
    else:
        raise RuntimeError("Auth throttling requires PostgreSQL or SQLite")
    db.execute(stmt)
    bucket = db.execute(
        select(AuthRateBucket).where(AuthRateBucket.key == key).with_for_update()
    ).scalar_one()
    if bucket.used >= LIMITS[action]:
        remaining = max(1, int((expires - now).total_seconds()))
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts",
            headers={"Retry-After": str(remaining)},
        )
    bucket.used += 1
    db.commit()  # Commit charge before login verification; never refund failures.
    return bucket.used
