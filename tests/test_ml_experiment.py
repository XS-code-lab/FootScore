"""Strict point-in-time snapshot and chronological ML experiment tests.

All labeled records below are SYNTHETIC. Test accuracy does not represent
real football forecasting performance.
"""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.ml.experiment import (
    DatasetError, score_experiment, train_chronological_experiment,
    validate_labeled_rows,
)
from app.ml.features import FEATURE_NAMES, TARGET_NAME, export_unlabeled
from app.models import Competition, Match, Player, PlayerMatchStats


def _record(player_id: int, cutoff_year: int, target: int,
            *, competition_id: int = 1, position: str = "attacker") -> dict:
    # Explicitly fabricated examples for offline contract tests ONLY.
    minutes = 90 * (1 + player_id % 7)
    rated = minutes if player_id % 8 else minutes // 2
    features = dict.fromkeys(FEATURE_NAMES)
    features.update({
        "age": 17 + player_id % 5,
        "minutes": minutes,
        "matches": 1 + player_id % 7,
        "rated_minutes": rated,
        "average_rating": None if rated == 0 else 5.5 + player_id % 5 * 0.35,
        "rating_coverage": rated / minutes,
        "goals_per90": player_id % 4 * 0.2,
        "assists_per90": player_id % 3 * 0.13,
        "shots_on_target_per90": player_id % 5 * 0.5,
        "key_passes_per90": None if player_id % 6 == 0 else player_id % 4 * 0.4,
        "successful_dribbles_per90": player_id % 3 * 0.5,
        "tackles_per90": None,
        "interceptions_per90": None,
        "saves_per90": None,
    })
    return {
        "player_id": player_id,
        "competition_id": competition_id,
        "position_group": position,
        "feature_cutoff": f"{cutoff_year}-06-30",
        "target_window_start": f"{cutoff_year}-07-01",
        "target_window_end": f"{cutoff_year + 1}-06-30",
        "target_name": TARGET_NAME,
        "features": features,
        "target": target,
        "point_in_time_verified": True,
        "coverage_verified": True,
        "label_source": "synthetic-test-verified-source",
        "label_verified_at": f"{cutoff_year + 1}-07-05",
    }


def _dataset() -> list[dict]:
    earlier = [
        _record(i + 1, 2022, 1 if i % 3 == 0 else 0)
        for i in range(42)
    ]
    later = [
        _record(i + 1, 2024, 1 if i % 4 == 0 else 0)
        for i in range(18)
    ]
    return earlier + later


def test_experiment_is_chronological_unapproved_and_reproducible() -> None:
    dataset = _dataset()
    artifact = train_chronological_experiment(dataset)
    assert artifact["approved_for_production"] is False
    assert artifact["model_type"] == "logistic_regression"
    assert artifact["target"] == TARGET_NAME
    assert artifact["train_cutoffs"] == ["2022-06-30"]
    assert artifact["validation_cutoff"] == "2024-06-30"
    assert artifact["train_count"] == 42
    assert artifact["validation_count"] == 18
    assert set(artifact["feature_names"]) == set(FEATURE_NAMES)
    assert len(artifact["imputer_medians"]) == len(FEATURE_NAMES)
    assert len(artifact["coefficients"]) == len(FEATURE_NAMES)
    assert 0 <= artifact["metrics"]["validation_roc_auc"] <= 1
    assert 0 <= artifact["metrics"]["validation_average_precision"] <= 1
    assert 0 <= artifact["metrics"]["validation_brier"] <= 1
    assert 0 <= artifact["metrics"]["constant_baseline_brier"] <= 1
    score = score_experiment(artifact, dataset[-1]["features"])
    assert 0 <= score <= 1
    assert abs(
        score - score_experiment(artifact, dataset[-1]["features"])
    ) < 1e-12
    rerun = train_chronological_experiment(dataset)
    assert rerun["dataset_fingerprint"] == artifact["dataset_fingerprint"]
    assert rerun["coefficients"] == artifact["coefficients"]


def test_refuse_unverified_missing_future_and_cross_league_labels() -> None:
    base = _dataset()
    unverified = deepcopy(base)
    unverified[0]["coverage_verified"] = False
    with pytest.raises(DatasetError, match="coverage"):
        train_chronological_experiment(unverified)
    unverified = deepcopy(base)
    unverified[0]["point_in_time_verified"] = False
    with pytest.raises(DatasetError, match="Point-in-time"):
        validate_labeled_rows(unverified)
    bogus = deepcopy(base)
    bogus[0]["target"] = None
    with pytest.raises(DatasetError, match="explicit verified"):
        validate_labeled_rows(bogus)
    bogus = deepcopy(base)
    bogus[0]["target"] = False
    with pytest.raises(DatasetError, match="explicit verified"):
        validate_labeled_rows(bogus)
    wrong_window = deepcopy(base)
    wrong_window[0]["target_window_start"] = "2021-07-01"
    with pytest.raises(DatasetError, match="NEXT"):
        validate_labeled_rows(wrong_window)
    too_early = deepcopy(base)
    too_early[0]["label_verified_at"] = "2023-06-30"
    with pytest.raises(DatasetError, match="after"):
        validate_labeled_rows(too_early)
    unknown_key = deepcopy(base)
    unknown_key[0]["features"]["future_rating"] = 10
    with pytest.raises(DatasetError, match="Feature keys"):
        validate_labeled_rows(unknown_key)
    duplicate = deepcopy(base)
    duplicate.append(deepcopy(duplicate[0]))
    with pytest.raises(DatasetError, match="Duplicate"):
        validate_labeled_rows(duplicate)
    mixed = deepcopy(base)
    mixed[-1]["competition_id"] = 2
    with pytest.raises(DatasetError, match="competition/position"):
        train_chronological_experiment(mixed)
    mixed = deepcopy(base)
    mixed[-1]["position_group"] = "goalkeeper"
    with pytest.raises(DatasetError, match="competition/position"):
        train_chronological_experiment(mixed)


def test_train_label_verified_only_after_holdout_cutoff_refused() -> None:
    records = _dataset()
    for row in records[:42]:
        row["feature_cutoff"] = "2023-06-30"
        row["target_window_start"] = "2023-07-01"
        row["target_window_end"] = "2024-06-30"
        row["label_verified_at"] = "2024-07-05"
    with pytest.raises(DatasetError, match="not available at holdout"):
        train_chronological_experiment(records)


def test_no_prediction_for_invalid_artifact() -> None:
    artifact = train_chronological_experiment(_dataset())
    with pytest.raises(DatasetError, match="Unsupported artifact"):
        score_experiment({**artifact, "schema": "unknown"}, _dataset()[0]["features"])
    with pytest.raises(DatasetError, match="Invalid inference"):
        score_experiment(artifact, {"future_goal_count": 12})


def test_export_does_not_claim_historical_verification() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        comp = Competition(name="Test League", provider="api-football", provider_id="9")
        player = Player(
            name="Junior", birth_date=date(2007, 4, 1),
            position="Attacker", provider="api-football", provider_id="99",
        )
        db.add_all([comp, player])
        db.flush()
        first = Match(
            competition_id=comp.id, provider="api-football", provider_id="1",
            kickoff=datetime(2024, 5, 20, tzinfo=timezone.utc), status="FT",
        )
        future = Match(
            competition_id=comp.id, provider="api-football", provider_id="2",
            kickoff=datetime(2024, 7, 20, tzinfo=timezone.utc), status="FT",
        )
        db.add_all([first, future])
        db.flush()
        for match, rating in ((first, 7.2), (future, 9.9)):
            db.add(PlayerMatchStats(
                match_id=match.id, player_id=player.id, minutes=90, goals=1,
                assists=0, rating=rating,
                payload={"games": {"position": "Attacker"},
                         "shots": {"on": 2}},
            ))
        db.flush()
        snapshots = export_unlabeled(
            db, cutoff=date(2024, 6, 30), competition_id=comp.id,
            position="attacker",
        )
        assert len(snapshots) == 1
        row = snapshots[0]
        assert row["features"]["minutes"] == 90
        assert row["features"]["average_rating"] == 7.2
        assert row["features"]["goals_per90"] == 1
        assert row["target"] is None
        assert row["point_in_time_verified"] is False
        assert row["coverage_verified"] is False
        assert row["label_verified_at"] is None
        with pytest.raises(DatasetError):
            validate_labeled_rows(snapshots)
        assert export_unlabeled(db, cutoff=date(2024, 6, 30), position="goalkeeper") == []
        with pytest.raises(ValueError, match="June 30"):
            export_unlabeled(db, cutoff=date(2024, 5, 30))
