"""Historical fetch receipts, provider quota and conservative ML outcome audits."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.ml.features import FEATURE_NAMES, TARGET_NAME
from app.ml.outcomes import propose_outcome_observations
from app.models import (
    HistoricalPlayerFetch, Match, Player, PlayerMatchStats, ProviderQuota,
)
from app.providers.api_football import normalize_fixture
from app.services.historical import (
    audit_fixture_player_coverage, plan_historical_backfill,
)
from app.services.ingestion import upsert_fixtures
from app.services.sync_players import sync_fixture_players
from app.settings import settings


def _fixture(fid: int, status: str = "FT", date_text: str = "2025-05-01") -> dict:
    return {
        "fixture": {
            "id": fid, "date": f"{date_text}T12:00:00Z",
            "status": {"short": status},
        },
        "league": {"id": 999, "name": "Test"},
        "teams": {
            "home": {"id": 10, "name": "Home"},
            "away": {"id": 20, "name": "Away"},
        },
        "goals": {"home": 2, "away": 0},
    }


def _database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed_fixtures(db: Session) -> dict:
    for fixture in (
        _fixture(101), _fixture(102), _fixture(103),
        _fixture(104, status="NS"),
        _fixture(105, date_text="2025-07-01"),
    ):
        upsert_fixtures(db, [normalize_fixture(fixture)])
    db.flush()
    return {str(match.provider_id): match for match in db.scalars(select(Match)).all()}


def test_historical_plan_and_coverage_are_conservative() -> None:
    engine = _database()
    with Session(engine) as db:
        by_external_id = _seed_fixtures(db)
        db.add_all([
            HistoricalPlayerFetch(
                match_id=by_external_id["101"].id, status="imported",
                players_imported=18, fetched_at=datetime.now(timezone.utc),
            ),
            HistoricalPlayerFetch(
                match_id=by_external_id["102"].id, status="empty",
                players_imported=0, fetched_at=datetime.now(timezone.utc),
            ),
        ])
        db.commit()
        from app.models import Competition

        competition = db.scalar(select(Competition))
        report = audit_fixture_player_coverage(
            db, competition_id=competition.id,
            start=date(2024, 7, 1), end=date(2025, 6, 30),
        )
        assert report["finished_fixtures_in_local_db"] == 3
        assert report["local_player_fetches"] == {
            "imported": 1, "empty": 1, "not_fetched": 1,
        }
        assert report["is_suitable_for_negative_ml_labels"] is False
        assert report["player_statistics_coverage_externally_verified"] is False
        no_retry = plan_historical_backfill(
            db, competition_id=competition.id,
            start=date(2024, 7, 1), end=date(2025, 6, 30),
        )
        assert no_retry["pending_count"] == 1
        assert no_retry["estimated_api_calls"] == 1
        assert no_retry["dry_run"] is True
        assert no_retry["selected"][0]["match_id"] == by_external_id["103"].id
        retry = plan_historical_backfill(
            db, competition_id=competition.id, start=date(2024, 7, 1),
            end=date(2025, 6, 30), retry_empty=True,
        )
        assert [row["match_id"] for row in retry["selected"]] == [
            by_external_id["102"].id, by_external_id["103"].id,
        ]
        assert db.scalar(select(func.count()).select_from(ProviderQuota)) == 0


def test_window_and_budget_validation() -> None:
    engine = _database()
    with Session(engine) as db:
        with pytest.raises(ValueError, match="historical window"):
            plan_historical_backfill(
                db, competition_id=1,
                start=date(2025, 7, 1), end=date(2025, 6, 30),
            )
        with pytest.raises(ValueError, match="future"):
            audit_fixture_player_coverage(
                db, competition_id=1,
                start=date(2026, 7, 1), end=date(2027, 6, 30),
            )
        with pytest.raises(ValueError, match="between 1 and 10"):
            plan_historical_backfill(
                db, competition_id=1,
                start=date(2024, 7, 1), end=date(2025, 6, 30),
                max_requests=999,
            )


def test_manual_fetch_receipts_are_not_coverage_certificates(monkeypatch) -> None:
    engine = _database()
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        matches = _seed_fixtures(db)
        selected = [matches["101"].id, matches["102"].id]
    monkeypatch.setattr("app.services.sync_players.SessionLocal", factory)
    monkeypatch.setattr(settings, "api_football_key", "mock-key")
    monkeypatch.setattr(settings, "provider_daily_limit", 5)
    monkeypatch.setattr(settings, "provider_reserve", 1)

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_fixture_players(self, *, fixture_id: int):
            if fixture_id == 101:
                return [{
                    "team": {"id": 10},
                    "players": [{
                        "player": {"id": 88, "name": "One Player"},
                        "statistics": [{
                            "games": {"minutes": 80, "rating": "7.5"},
                            "goals": {"total": 1, "assists": 0},
                        }],
                    }],
                }]
            if fixture_id == 102:
                return []  # Could mean missing provider coverage, NOT 0 participants.
            raise AssertionError("Unexpected provider request")

        def close(self):
            pass

    monkeypatch.setattr("app.services.sync_players.ApiFootballClient", FakeProvider)
    assert sync_fixture_players(selected[0]).imported == 1
    assert sync_fixture_players(selected[1]).imported == 0
    with factory() as db:
        receipts = db.scalars(select(HistoricalPlayerFetch)).all()
        assert {r.status for r in receipts} == {"imported", "empty"}
        assert db.scalar(select(ProviderQuota.used)) == 2
        assert db.scalar(select(func.count()).select_from(PlayerMatchStats)) == 1
        assert all(r.players_imported >= 0 for r in receipts)
        from app.models import Competition

        comp = db.scalar(select(Competition))
        plan = plan_historical_backfill(
            db, competition_id=comp.id,
            start=date(2024, 7, 1), end=date(2025, 6, 30),
        )
        assert plan["estimated_api_calls"] == 1


def test_outcome_proposals_preserve_unknown_negative_labels() -> None:
    engine = _database()
    with Session(engine) as db:
        _seed_fixtures(db)
        from app.models import Competition

        comp = db.scalar(select(Competition))
        positive = Player(
            name="Positive", provider="api-football", provider_id="500",
            birth_date=date(2006, 2, 1),
        )
        missing = Player(
            name="Missing", provider="api-football", provider_id="501",
            birth_date=date(2006, 2, 1),
        )
        db.add_all([positive, missing])
        db.flush()
        for idx in range(11):
            dt = datetime(2025, 2, 1, tzinfo=timezone.utc) + timedelta(days=idx)
            match = Match(
                competition_id=comp.id, kickoff=dt, status="FT",
                provider="api-football", provider_id=str(5000 + idx),
            )
            db.add(match)
            db.flush()
            db.add(PlayerMatchStats(
                match_id=match.id, player_id=positive.id, minutes=90,
                rating=7.2, goals=0, assists=0,
            ))
        db.flush()
        ids = (positive.id, missing.id)
        input_rows = []
        for pid in ids:
            input_rows.append({
                "player_id": pid,
                "competition_id": comp.id,
                "position_group": "attacker",
                "feature_cutoff": "2024-06-30",
                "target_window_start": "2024-07-01",
                "target_window_end": "2025-06-30",
                "target_name": TARGET_NAME,
                "features": dict.fromkeys(FEATURE_NAMES),
                "target": None,
                "point_in_time_verified": True,  # Refuse to trust even caller claims.
                "coverage_verified": True,
                "label_source": "external-source",
                "label_verified_at": "2025-07-05",
            })
        proposals = propose_outcome_observations(db, input_rows)
        assert proposals[0]["outcome_observation"]["observed_minutes"] == 990
        assert proposals[0]["outcome_observation"]["observed_threshold_reached"] is True
        assert proposals[1]["outcome_observation"]["observed_minutes"] == 0
        assert proposals[1]["outcome_observation"]["observed_threshold_reached"] is False
        for row in proposals:
            assert row["target"] is None
            assert row["label_source"] is None
            assert row["point_in_time_verified"] is False
            assert row["coverage_verified"] is False
            assert row["outcome_observation"]["suggested_label"] is None
        with pytest.raises(ValueError, match="already labeled"):
            propose_outcome_observations(db, [{**input_rows[0], "target": 1}])
        with pytest.raises(ValueError, match="unfinished future"):
            propose_outcome_observations(db, [{
                **input_rows[0], "feature_cutoff": "2026-06-30",
                "target_window_start": "2026-07-01",
                "target_window_end": "2027-06-30",
            }])
