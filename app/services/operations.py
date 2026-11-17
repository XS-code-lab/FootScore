"""Read-only operational snapshot for trusted CLI operators.

Never expose queue contents, account identifiers, tokens, SMTP addresses,
provider keys or individual match/player records. This is not a public API.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EmailDelivery, LiveUpdate, ProviderQuota, ProviderRequestLog
from app.settings import settings


def _utc(value: datetime) -> datetime:
    # SQLite tests/dev return timezone-naive timestamps stored as UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else (
        value.astimezone(timezone.utc)
    )


def _oldest_age(now: datetime, timestamp: datetime | None) -> int | None:
    if timestamp is None:
        return None
    return max(0, int((now - _utc(timestamp)).total_seconds()))


def snapshot(db: Session, *, now: datetime | None = None) -> dict:
    """A low-cardinality summary of current *local database* state.

    No Redis or SMTP network request is made here. The account-wide provider
    quota, Redis subscriber count and external inbox delivery are unknown.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Operational snapshot timestamp must be timezone-aware")
    now = now.astimezone(timezone.utc)

    live_count, live_oldest = db.execute(
        select(func.count(), func.min(LiveUpdate.created_at)).where(
            LiveUpdate.published_at.is_(None),
        )
    ).one()
    email_pending, email_oldest = db.execute(
        select(func.count(), func.min(EmailDelivery.created_at)).where(
            EmailDelivery.status == "pending",
        )
    ).one()
    email_due = db.scalar(
        select(func.count()).select_from(EmailDelivery).where(
            EmailDelivery.status == "pending",
            EmailDelivery.next_attempt_at <= now,
        )
    ) or 0
    email_failed = db.scalar(
        select(func.count()).select_from(EmailDelivery).where(
            EmailDelivery.status == "failed",
        )
    ) or 0
    local_daily = db.scalar(
        select(ProviderQuota.used).where(
            ProviderQuota.provider == "api-football",
            ProviderQuota.day == now.date(),
        )
    ) or 0
    last_minute = db.scalar(
        select(func.count()).select_from(ProviderRequestLog).where(
            ProviderRequestLog.provider == "api-football",
            ProviderRequestLog.reserved_at > now - timedelta(seconds=60),
            ProviderRequestLog.reserved_at <= now,
        )
    ) or 0
    return {
        "observed_at": now.isoformat(),
        "source": "local_database_only",
        "match_outbox": {
            "unpublished": live_count,
            "oldest_unpublished_age_seconds": _oldest_age(now, live_oldest),
        },
        "security_email_outbox": {
            "pending": email_pending,
            "due_now": email_due,
            "failed": email_failed,
            "oldest_pending_age_seconds": _oldest_age(now, email_oldest),
        },
        "api_football_local_quota": {
            "used_today": local_daily,
            "reserved_last_60_seconds": last_minute,
            "daily_available_after_reserve": max(
                0, settings.provider_daily_limit - settings.provider_reserve - local_daily,
            ),
            "minute_available": max(
                0, settings.provider_per_minute_limit - last_minute,
            ),
            "provider_account_usage_verified": False,
        },
        "redis_reachable": None,
        "smtp_deliverability_verified": False,
    }


def assess_snapshot(
    data: dict, *, max_match_age: int = 120, max_email_age: int = 300,
) -> dict:
    """Return clear operator alerts, not a production availability promise.

    Match and email workers are optional. An alert may simply mean that
    a worker profile was deliberately not enabled.
    """
    if max_match_age < 1 or max_email_age < 1:
        raise ValueError("Alert thresholds must be positive")
    alerts = []
    live = data["match_outbox"]
    email = data["security_email_outbox"]
    if (live["unpublished"] > 0
            and live["oldest_unpublished_age_seconds"] is not None
            and live["oldest_unpublished_age_seconds"] > max_match_age):
        alerts.append("match_outbox_delayed")
    if (email["due_now"] > 0
            and email["oldest_pending_age_seconds"] is not None
            and email["oldest_pending_age_seconds"] > max_email_age):
        alerts.append("security_email_delivery_delayed")
    if email["failed"] > 0:
        alerts.append("security_email_failed_items")
    return {
        "state": "attention" if alerts else "ok",
        "alerts": alerts,
        "thresholds_seconds": {
            "match": max_match_age, "email": max_email_age,
        },
        "note": (
            "Local snapshot only. A clear result does not verify Redis, "
            "SMTP delivery, upstream provider availability or data freshness."
        ),
    }
