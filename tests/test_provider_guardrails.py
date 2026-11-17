"""Rolling provider rate limits and advisory league capability probes; no live HTTP."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import LeagueCoverageProbe, ProviderQuota, ProviderRequestLog
from app.providers.api_football import ApiFootballClient
from app.services.league_probe import (
    coverage_report, parse_league_coverage, probe_league, record_league_coverage,
)
from app.services.provider_guardrails import ProviderRateLimited, reserve_provider_call
from app.services.quota import QuotaExceeded
from app.settings import settings


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_rolling_sixty_seconds_and_daily_limit_rollbacks() -> None:
    engine = _engine()
    initial = datetime(2026, 11, 17, 12, 0, 59, tzinfo=timezone.utc)
    for n in range(3):
        with Session(engine) as db:
            assert reserve_provider_call(
                db, at=initial + timedelta(seconds=n),
                minute_limit=3, daily_limit=10, daily_reserve=2,
            ) == n + 1
            db.commit()
    # Crossing an UTC-minute boundary does NOT reset the sliding window.
    with Session(engine) as db:
        with pytest.raises(ProviderRateLimited, match="rolling"):
            reserve_provider_call(
                db, at=initial + timedelta(seconds=3),
                minute_limit=3, daily_limit=10, daily_reserve=2,
            )
        db.rollback()
        assert db.scalar(select(ProviderQuota.used)) == 3
        assert db.scalar(select(func.count()).select_from(ProviderRequestLog)) == 3

    with Session(engine) as db:
        assert reserve_provider_call(
            db, at=initial + timedelta(seconds=61),
            minute_limit=3, daily_limit=10, daily_reserve=2,
        ) == 4
        db.commit()
        assert db.scalar(select(ProviderQuota.used)) == 4


def test_daily_guardrail_failure_does_not_commit_minute_log() -> None:
    engine = _engine()
    at = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        assert reserve_provider_call(
            db, at=at, minute_limit=8, daily_limit=2, daily_reserve=1,
        ) == 1
        db.commit()
    with Session(engine) as db:
        with pytest.raises(QuotaExceeded):
            reserve_provider_call(
                db, at=at + timedelta(seconds=1),
                minute_limit=8, daily_limit=2, daily_reserve=1,
            )
        db.rollback()
        assert db.scalar(select(func.count()).select_from(ProviderRequestLog)) == 1
        assert db.scalar(select(ProviderQuota.used)) == 1


def test_timestamp_and_settings_validation() -> None:
    engine = _engine()
    with Session(engine) as db:
        with pytest.raises(ValueError, match="timezone-aware"):
            reserve_provider_call(db, at=datetime(2026, 11, 17, 12, 0))
        with pytest.raises(ValueError, match="between 1 and 10"):
            reserve_provider_call(db, minute_limit=11)
        assert db.scalar(select(func.count()).select_from(ProviderRequestLog)) == 0


def test_provider_league_endpoint_and_advisory_unknowns() -> None:
    response = [{
        "league": {"id": 39},
        "seasons": [
            {"year": 2024, "coverage": {"fixtures": {
                "events": True, "statistics_fixtures": True,
                "statistics_players": False, "lineups": None,
            }}},
        ],
    }]
    def handler(request: httpx.Request):
        assert request.url.path == "/leagues"
        assert request.url.params["id"] == "39"
        assert request.url.params["season"] == "2024"
        return httpx.Response(200, json={"errors": [], "response": response})

    client = ApiFootballClient("test", transport=httpx.MockTransport(handler))
    try:
        assert client.fetch_league_coverage(league=39, season=2024) == response
    finally:
        client.close()
    assert parse_league_coverage(response, 39, 2024) == {
        "events": True,
        "statistics_fixtures": True,
        "statistics_players": False,
        "lineups": None,
    }
    assert all(
        v is None for v in parse_league_coverage(response, 40, 2024).values()
    )

    engine = _engine()
    with Session(engine) as db:
        empty = coverage_report(db, 39, 2024)
        assert not empty["probed"]
        assert not empty["usable_for_ml_negative_labels"]
        probe = record_league_coverage(db, 39, 2024, response)
        db.commit()
        assert probe.reported_statistics_players is False
        report = coverage_report(db, 39, 2024)
        assert report["probed"]
        assert report["capabilities"]["events"] is True
        assert report["capabilities"]["lineups"] is None
        assert report["all_fixtures_verified"] is False
        assert report["all_player_stats_verified"] is False
        assert db.scalar(select(func.count()).select_from(LeagueCoverageProbe)) == 1
        record_league_coverage(db, 39, 2024, [{
            "league": {"id": 39}, "seasons": [
                {"year": 2024, "coverage": {"fixtures": {
                    "statistics_players": True,
                }}},
            ],
        }])
        db.commit()
        assert db.scalar(select(func.count()).select_from(LeagueCoverageProbe)) == 1
        assert coverage_report(db, 39, 2024)["capabilities"]["statistics_players"] is True


def test_probe_requires_explicit_key_and_quota(monkeypatch) -> None:
    engine = _engine()
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.league_probe.SessionLocal", factory)
    monkeypatch.setattr(settings, "api_football_key", None)
    with pytest.raises(RuntimeError, match="API_FOOTBALL_KEY"):
        probe_league(39, 2024)

    monkeypatch.setattr(settings, "api_football_key", "mock-only")
    monkeypatch.setattr(settings, "provider_daily_limit", 2)
    monkeypatch.setattr(settings, "provider_reserve", 1)
    monkeypatch.setattr(settings, "provider_per_minute_limit", 8)

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_league_coverage(self, *, league: int, season: int):
            return [{"league": {"id": league}, "seasons": [{
                "year": season, "coverage": {"fixtures": {
                    "events": True, "statistics_players": True,
                }},
            }]}]

        def close(self):
            pass

    monkeypatch.setattr("app.services.league_probe.ApiFootballClient", FakeProvider)
    report = probe_league(39, 2024)
    assert report["probed"]
    assert report["capabilities"]["statistics_players"] is True
    assert not report["usable_for_ml_negative_labels"]
    with factory() as db:
        assert db.scalar(select(ProviderQuota.used)) == 1
    with pytest.raises(QuotaExceeded):
        probe_league(39, 2024)
