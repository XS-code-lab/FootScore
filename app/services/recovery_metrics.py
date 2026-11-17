"""Honest, low-cardinality evidence from an isolated backup restore drill.

Duration measurements are local process observations, NOT a production RTO,
a recovery point objective, or a guarantee that all source data is present.
"""
from datetime import datetime, timezone

PHASES = ("download_authenticate", "decrypt", "postgres_restore", "schema_audit")


def recovery_measurements(
    phase_seconds: dict[str, float], *, elapsed_seconds: float,
    budget_seconds: int | None = None, observed_at: datetime | None = None,
) -> dict:
    if set(phase_seconds) != set(PHASES):
        raise ValueError("Recovery evidence requires four named phases")
    if any(
        not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
        or not 0 <= seconds < 100000
        for seconds in (*phase_seconds.values(), elapsed_seconds)
    ):
        raise ValueError("Recovery durations must be finite, nonnegative numbers")
    if budget_seconds is not None and not 1 <= budget_seconds <= 3600:
        raise ValueError("Duration budget must be between 1 and 3600 seconds")
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Measurement observation time must be timezone-aware")
    elapsed = round(float(elapsed_seconds), 3)
    within = elapsed <= budget_seconds if budget_seconds is not None else None
    return {
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "phase_seconds": {name: round(float(phase_seconds[name]), 3) for name in PHASES},
        "total_elapsed_seconds": elapsed,
        "operator_drill_budget_seconds": budget_seconds,
        "operator_drill_budget_met": within,
        "production_rto_verified": False,
        "production_rpo_verified": False,
        "complete_source_data_verified": False,
    }
