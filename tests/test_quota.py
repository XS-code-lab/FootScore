from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ProviderQuota
from app.services.quota import QuotaExceeded, reserve_request


def test_atomic_quota_limit_and_utc_day() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    day = date(2026, 11, 17)
    for expected in (1, 2):
        with Session(engine) as session:
            assert reserve_request(session, day=day, limit=3, reserve=1) == expected
            session.commit()
    with Session(engine) as session:
        with pytest.raises(QuotaExceeded):
            reserve_request(session, day=day, limit=3, reserve=1)
        session.rollback()
        assert session.scalar(select(ProviderQuota.used)) == 2
    with Session(engine) as session:
        assert reserve_request(
            session, day=date(2026, 11, 18), limit=3, reserve=1,
        ) == 1
        session.commit()


def test_zero_available_budget() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(QuotaExceeded):
            reserve_request(session, limit=10, reserve=10)
