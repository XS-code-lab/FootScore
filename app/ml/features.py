"""Generate U22 feature snapshots WITHOUT making or guessing future labels.

Exported rows are explicitly unverified: today's database usually contains
late corrections/backfills, so a historical-looking cutoff isn't automatically
a genuine point-in-time observation.
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.scouting import age_on
from app.services.scouting_discovery import _collect, _features

FEATURE_NAMES = (
    "age", "minutes", "matches", "rated_minutes", "average_rating",
    "rating_coverage", "goals_per90", "assists_per90",
    "shots_on_target_per90", "key_passes_per90",
    "successful_dribbles_per90", "tackles_per90",
    "interceptions_per90", "saves_per90",
)
TARGET_NAME = "next_season_900_minutes_in_same_competition"


def validate_cutoff(cutoff: date) -> None:
    if (cutoff.month, cutoff.day) != (6, 30):
        raise ValueError("Use a June 30 season cutoff for this experiment")


def export_unlabeled(
    db: Session, *, cutoff: date, competition_id: int | None = None,
    position: str | None = None, min_minutes: int = 30,
) -> list[dict]:
    """One record per player, league and broad position group at a cutoff.

    Target and proof fields are blank until a trusted annotator has audited
    BOTH historical feature availability and future competition coverage.
    """
    validate_cutoff(cutoff)
    if competition_id is not None and competition_id <= 0:
        raise ValueError("Invalid competition")
    if position not in {None, "attacker", "midfielder", "defender", "goalkeeper"}:
        raise ValueError("Invalid position")
    if min_minutes < 1:
        raise ValueError("Minimum minutes must be positive")

    totals, players = _collect(db, cutoff, competition_id)
    end = date(cutoff.year + 1, 6, 30)
    start = cutoff + timedelta(days=1)
    rows = []
    for (league, group, pid), total in sorted(totals.items()):
        player = players[pid]
        if player.birth_date is None:
            continue
        age = age_on(player.birth_date, cutoff)
        if not 0 <= age < 22 or total["minutes"] < min_minutes:
            continue
        if position is not None and group != position:
            continue

        per90, _ = _features(total)
        rated_minutes = total["rated_minutes"]
        features = {
            "age": age,
            "minutes": total["minutes"],
            "matches": total["matches"],
            "rated_minutes": rated_minutes,
            "average_rating": round(total["rating_sum"] / rated_minutes, 4)
            if rated_minutes else None,
            "rating_coverage": round(rated_minutes / total["minutes"], 4),
            **per90,
        }
        if set(features) != set(FEATURE_NAMES):
            raise RuntimeError("Feature contract changed; update FEATURE_NAMES")
        rows.append({
            "player_id": pid,
            "competition_id": league,
            "position_group": group,
            "feature_cutoff": cutoff.isoformat(),
            "target_window_start": start.isoformat(),
            "target_window_end": end.isoformat(),
            "target_name": TARGET_NAME,
            "features": features,
            "target": None,
            "point_in_time_verified": False,
            "coverage_verified": False,
            "label_source": None,
            "label_verified_at": None,
        })
    return rows
