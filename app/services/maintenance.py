"""Bounded retention sweep for stale authentication state."""
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AuthOneTimeToken, AuthRateBucket, EmailDelivery


def cleanup_auth_state(
    db: Session, *, execute: bool = False, limit: int = 1000,
    now: datetime | None = None,
) -> dict:
    if not 1 <= limit <= 10000:
        raise ValueError("Cleanup limit must be 1..10000")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Now must be timezone-aware")
    expired = db.scalars(
        select(AuthRateBucket).where(AuthRateBucket.expires_at < now)
        .order_by(AuthRateBucket.expires_at, AuthRateBucket.key)
        .limit(limit)
    ).all()
    remaining = max(0, limit - len(expired))
    tokens = db.scalars(
        select(AuthOneTimeToken).where(AuthOneTimeToken.expires_at < now)
        .order_by(AuthOneTimeToken.expires_at, AuthOneTimeToken.id)
        .limit(remaining)
    ).all() if remaining else []
    result = {
        "dry_run": not execute,
        "expired_buckets_selected": len(expired),
        "expired_tokens_selected": len(tokens),
        "max_rows": limit,
    }
    if execute:
        # Remove encrypted delivery payloads BEFORE deleting their token
        # parent rows, so foreign keys work on both PostgreSQL and SQLite.
        token_ids = [row.id for row in tokens]
        if token_ids:
            db.execute(delete(EmailDelivery).where(
                EmailDelivery.token_id.in_(token_ids),
            ))
        for row in (*expired, *tokens):
            db.delete(row)
        db.commit()
    return result
