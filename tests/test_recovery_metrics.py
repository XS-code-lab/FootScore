"""Recovery timing evidence is an observation, not a production RTO promise."""
from datetime import datetime, timezone

import pytest

from app.services.recovery_metrics import PHASES, recovery_measurements


def _phases() -> dict[str, float]:
    return dict(zip(PHASES, [0.15, 0.25, 1.5, 0.1], strict=True))


def test_complete_report_has_phase_observations_and_honest_limits() -> None:
    sample = recovery_measurements(
        _phases(), elapsed_seconds=2.0999,
        budget_seconds=5,
        observed_at=datetime(2026, 11, 18, 12, tzinfo=timezone.utc),
    )
    assert sample["phase_seconds"] == {
        "download_authenticate": 0.15,
        "decrypt": 0.25,
        "postgres_restore": 1.5,
        "schema_audit": 0.1,
    }
    assert sample["total_elapsed_seconds"] == 2.1
    assert sample["operator_drill_budget_seconds"] == 5
    assert sample["operator_drill_budget_met"] is True
    assert not sample["production_rto_verified"]
    assert not sample["production_rpo_verified"]
    assert not sample["complete_source_data_verified"]
    assert sample["observed_at"] == "2026-11-18T12:00:00+00:00"


def test_duration_budget_failure_is_visible_not_a_false_success() -> None:
    sample = recovery_measurements(
        _phases(), elapsed_seconds=20.1, budget_seconds=10,
    )
    assert sample["operator_drill_budget_met"] is False
    assert sample["production_rto_verified"] is False
    default = recovery_measurements(_phases(), elapsed_seconds=3.0)
    assert default["operator_drill_budget_met"] is None
    assert default["operator_drill_budget_seconds"] is None


@pytest.mark.parametrize("bad", [
    {"download_authenticate": 1.0},
    {**_phases(), "extraneous": 1.0},
])
def test_incomplete_or_extra_phases_rejected(bad) -> None:
    with pytest.raises(ValueError, match="four named phases"):
        recovery_measurements(bad, elapsed_seconds=3)


@pytest.mark.parametrize("invalid", [-1, float("inf"), float("nan"), True])
def test_invalid_duration_rejected(invalid) -> None:
    with pytest.raises(ValueError, match="durations"):
        recovery_measurements(_phases(), elapsed_seconds=invalid)


@pytest.mark.parametrize("invalid", [0, 3601])
def test_invalid_budget_rejected(invalid: int) -> None:
    with pytest.raises(ValueError, match="budget"):
        recovery_measurements(
            _phases(), elapsed_seconds=3, budget_seconds=invalid,
        )


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        recovery_measurements(
            _phases(), elapsed_seconds=3, observed_at=datetime(2026, 11, 18),
        )
