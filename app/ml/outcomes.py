"""Propose NON-VERIFIED next-season outcome observations for human review."""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.features import TARGET_NAME, validate_cutoff
from app.models import Match, PlayerMatchStats
from app.services.scouting import FINAL_STATUSES


def propose_outcome_observations(db: Session, records: list[dict]) -> list[dict]:
    """Attach observed minutes and a positive-threshold hint, never training labels.

    Every target remains None. Neither positive nor negative outcomes may be
    certified from a partial local provider archive.
    """
    results = []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Every input must be an exported record")
        if row.get("target") is not None:
            raise ValueError("Refusing to overwrite an already labeled record")
        if row.get("target_name") != TARGET_NAME:
            raise ValueError("Unsupported target definition")
        try:
            cutoff = date.fromisoformat(row["feature_cutoff"])
            start = date.fromisoformat(row["target_window_start"])
            end = date.fromisoformat(row["target_window_end"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("Invalid outcome window dates") from exc
        validate_cutoff(cutoff)
        if start != date(cutoff.year, 7, 1) or end != date(cutoff.year + 1, 6, 30):
            raise ValueError("Outcome window must be the next July-June season")
        if end > datetime.now(timezone.utc).date():
            raise ValueError("Cannot inspect unfinished future outcome windows")
        player_id = row.get("player_id")
        competition_id = row.get("competition_id")
        if (type(player_id) is not int or player_id < 1
                or type(competition_id) is not int or competition_id < 1):
            raise ValueError("Missing local player or competition identity")
        first = datetime.combine(start, time.min, tzinfo=timezone.utc)
        last = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
        appearances = db.scalars(
            select(PlayerMatchStats)
            .join(Match, Match.id == PlayerMatchStats.match_id)
            .where(
                PlayerMatchStats.player_id == player_id,
                Match.competition_id == competition_id,
                Match.status.in_(FINAL_STATUSES),
                Match.kickoff >= first,
                Match.kickoff < last,
            )
        ).all()
        observed_minutes = sum(
            stat.minutes for stat in appearances
            if stat.minutes is not None and stat.minutes >= 0
        )
        missing_minutes = sum(
            1 for stat in appearances if stat.minutes is None or stat.minutes < 0
        )
        enriched = {**row}
        # Deliberately override any supplied verification flags.
        enriched.update({
            "target": None,
            "point_in_time_verified": False,
            "coverage_verified": False,
            "label_source": None,
            "label_verified_at": None,
            "outcome_observation": {
                "observed_minutes": observed_minutes,
                "appearance_rows": len(appearances),
                "appearance_rows_without_minutes": missing_minutes,
                "observed_threshold_reached": observed_minutes >= 900,
                "suggested_label": None,
                "independent_fixture_catalog_verified": False,
                "independent_player_coverage_verified": False,
                "note": (
                    "For audit review only. A threshold reached in local data is "
                    "evidence, not an automatically verified training label. "
                    "A threshold NOT reached does not mean a negative outcome."
                ),
            },
        })
        results.append(enriched)
    return results
