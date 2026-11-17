"""Provider-wide rolling sixty-second request reservations.

All in-app provider callers reserve daily AND minute budgets in one DB
transaction. Never catch an over-budget error *inside* that transaction
and commit: the context manager must roll back the daily reservation.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import ProviderRateLock, ProviderRequestLog
from app.services.quota import QuotaExceeded, reserve_request
from app.settings import settings

WINDOW = timedelta(seconds=60)


class ProviderRateLimited(QuotaExceeded):
    """Local rolling 60-second budget exhausted."""


def reserve_provider_call(
    db: Session, *, provider: str = "api-football",
    at: datetime | None = None, minute_limit: int | None = None,
    daily_limit: int | None = None, daily_reserve: int | None = None,
) -> int:
    """Reserve one request atomically, returning daily used count.

    PostgreSQL uses a provider lock row to serialize concurrent workers.
    SQLite serializes the write transaction; SQLite isn't a production
    multi-process deployment database. Commit before the HTTP request.
    """
    # Explicit timestamps exist only for deterministic tests. In normal
    # operation the clock MUST be sampled after acquiring the provider lock.
    # Sampling before locking lets waiting transactions count only earlier
    # reservations, and can overrun the 60-second budget under concurrency.
    if at is not None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Reservation timestamp must be timezone-aware")
        at = at.astimezone(timezone.utc)
    per_minute = (settings.provider_per_minute_limit
                  if minute_limit is None else minute_limit)
    if per_minute < 1 or per_minute > 10:
        raise ValueError("Configured minute limit must be between 1 and 10")
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(ProviderRateLock).values(
            provider=provider
        ).on_conflict_do_nothing(index_elements=["provider"])
    elif dialect == "sqlite":
        stmt = sqlite_insert(ProviderRateLock).values(
            provider=provider
        ).on_conflict_do_nothing(index_elements=["provider"])
    else:
        raise RuntimeError(f"Unsupported provider quota dialect: {dialect}")

    # Acquiring the write/row lock before counting prevents concurrent
    # workers from both observing the last free slot.
    db.execute(stmt)
    db.execute(
        select(ProviderRateLock).where(
            ProviderRateLock.provider == provider
        ).with_for_update()
    ).scalar_one()

    if at is None:
        # PostgreSQL's clock_timestamp() is evaluated NOW rather than at
        # transaction start (unlike now()/CURRENT_TIMESTAMP), and provides
        # one clock for all FootScore workers sharing this database.
        at = (
            db.scalar(select(func.clock_timestamp()))
            if dialect == "postgresql" else datetime.now(timezone.utc)
        )
        at = at.astimezone(timezone.utc)

    active = db.scalar(
        select(func.count()).select_from(ProviderRequestLog).where(
            ProviderRequestLog.provider == provider,
            ProviderRequestLog.reserved_at > at - WINDOW,
            ProviderRequestLog.reserved_at <= at,
        )
    ) or 0
    if active >= per_minute:
        raise ProviderRateLimited(
            f"{provider} rolling 60-second request budget exhausted"
        )
    used = reserve_request(
        db, provider=provider, day=at.date(),
        limit=settings.provider_daily_limit if daily_limit is None else daily_limit,
        reserve=settings.provider_reserve if daily_reserve is None else daily_reserve,
    )
    db.add(ProviderRequestLog(provider=provider, reserved_at=at))
    db.flush()
    return used
