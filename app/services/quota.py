"""Atomic daily quota reservations for PostgreSQL and SQLite."""
from datetime import date, datetime, timezone

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import ProviderQuota


class QuotaExceeded(RuntimeError):
    pass


def reserve_request(
    db: Session, *,
    provider: str = "api-football",
    day: date | None = None,
    limit: int = 100,
    reserve: int = 10,
) -> int:
    if day is None:
        day = datetime.now(timezone.utc).date()
    available = limit - reserve
    if available <= 0:
        raise QuotaExceeded("No request budget available")
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(ProviderQuota).values(
            provider=provider, day=day, used=0,
        ).on_conflict_do_nothing(index_elements=["provider", "day"])
    elif dialect == "sqlite":
        stmt = sqlite_insert(ProviderQuota).values(
            provider=provider, day=day, used=0,
        ).on_conflict_do_nothing(index_elements=["provider", "day"])
    else:
        raise RuntimeError(f"Unsupported quota dialect: {dialect}")
    db.execute(stmt)
    result = db.execute(
        update(ProviderQuota)
        .where(
            ProviderQuota.provider == provider,
            ProviderQuota.day == day,
            ProviderQuota.used < available,
        )
        .values(used=ProviderQuota.used + 1)
        .returning(ProviderQuota.used)
    )
    used = result.scalar_one_or_none()
    if used is None:
        raise QuotaExceeded(f"{provider} quota exhausted for {day}")
    return int(used)
