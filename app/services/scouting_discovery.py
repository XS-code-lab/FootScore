"""Auditable U22 talent discovery: league-and-position peer context, not AI.

All evidence comes from completed provider match records. Scores are *relative*
within a competition/position cohort, not probabilities of future success.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import fmean, pstdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Player, PlayerMatchStats
from app.services.scouting import FINAL_STATUSES, age_on

MODEL_VERSION = "cohort-rating-v1"
LOOKBACK_DAYS = 365
SHRINK_MINUTES = 270
PEER_MINUTES = 270
MIN_COVERAGE = 0.7
METRICS = {
    "shots_on_target": ("shots", "on"),
    "key_passes": ("passes", "key"),
    "successful_dribbles": ("dribbles", "success"),
    "tackles": ("tackles", "total"),
    "interceptions": ("tackles", "interceptions"),
    "saves": ("goals", "saves"),
}

GROUPS = {
    "attacker": "attacker", "forward": "attacker", "striker": "attacker",
    "a": "attacker", "fw": "attacker", "st": "attacker",
    "midfielder": "midfielder", "midfield": "midfielder", "m": "midfielder",
    "mf": "midfielder",
    "defender": "defender", "defence": "defender", "defense": "defender",
    "d": "defender", "df": "defender",
    "goalkeeper": "goalkeeper", "keeper": "goalkeeper", "g": "goalkeeper",
    "gk": "goalkeeper",
}


def position_group(value: str | None) -> str | None:
    if value is None:
        return None
    return GROUPS.get(value.strip().lower())


def _number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _new_total() -> dict:
    return {
        "minutes": 0, "rated_minutes": 0, "rating_sum": 0.0,
        "matches": 0, "rated_matches": 0,
        "goals": 0, "assists": 0, "goals_coverage_minutes": 0,
        "assists_coverage_minutes": 0, "metric_values": defaultdict(int),
        "metric_minutes": defaultdict(int),
    }


def _collect(
    db: Session, as_of: date, competition_id: int | None,
) -> tuple[dict, dict[int, Player]]:
    end = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    query = (
        select(Player, PlayerMatchStats, Match)
        .join(PlayerMatchStats, PlayerMatchStats.player_id == Player.id)
        .join(Match, Match.id == PlayerMatchStats.match_id)
        .where(
            Match.status.in_(FINAL_STATUSES),
            Match.kickoff.is_not(None),
            Match.kickoff >= start,
            Match.kickoff <= end,
            Match.competition_id.is_not(None),
        )
    )
    if competition_id is not None:
        query = query.where(Match.competition_id == competition_id)
    totals = defaultdict(_new_total)
    players = {}
    for player, stats, match in db.execute(query):
        minutes = stats.minutes
        if minutes is None or not 0 < minutes <= 130:
            continue
        raw_position = (stats.payload or {}).get("games") or {}
        group = position_group(raw_position.get("position")) or position_group(player.position)
        if group is None:
            continue
        key = (match.competition_id, group, player.id)
        total = totals[key]
        players[player.id] = player
        total["minutes"] += minutes
        total["matches"] += 1
        if stats.rating is not None and 0 <= stats.rating <= 10:
            total["rated_minutes"] += minutes
            total["rated_matches"] += 1
            total["rating_sum"] += stats.rating * minutes
        if stats.goals is not None and stats.goals >= 0:
            total["goals"] += stats.goals
            total["goals_coverage_minutes"] += minutes
        if stats.assists is not None and stats.assists >= 0:
            total["assists"] += stats.assists
            total["assists_coverage_minutes"] += minutes
        detail = stats.payload or {}
        for name, (section, field) in METRICS.items():
            raw = (detail.get(section) or {}).get(field)
            parsed = _number(raw)
            if parsed is not None:
                total["metric_values"][name] += parsed
                total["metric_minutes"][name] += minutes
    return totals, players


def _features(total: dict) -> tuple[dict, dict]:
    minutes = total["minutes"]
    features = {}
    coverage = {}
    for feature, numerator, counted_minutes in (
        ("goals_per90", total["goals"], total["goals_coverage_minutes"]),
        ("assists_per90", total["assists"], total["assists_coverage_minutes"]),
        *[
            (f"{name}_per90", total["metric_values"][name], total["metric_minutes"][name])
            for name in METRICS
        ],
    ):
        ratio = counted_minutes / minutes
        coverage[feature] = round(ratio, 3)
        features[feature] = (
            round(numerator * 90 / counted_minutes, 2)
            if counted_minutes > 0 and ratio >= MIN_COVERAGE else None
        )
    return features, coverage


def discover_u22(
    db: Session, *, as_of: date | None = None, category: str = "emerging",
    min_minutes: int = 300, min_cohort: int = 5,
    competition_id: int | None = None, position: str | None = None,
    limit: int = 50, player_id: int | None = None,
) -> dict:
    """Group-scoped discovery, deliberately NO global cross-league ranking."""
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()
    if category not in {"emerging", "established"}:
        raise ValueError("Invalid category")
    if not 60 <= min_minutes <= 5000 or not 3 <= min_cohort <= 100:
        raise ValueError("Invalid sample/cohort settings")
    if not 1 <= limit <= 100:
        raise ValueError("Invalid result limit")
    if position is not None and position not in set(GROUPS.values()):
        raise ValueError("Unknown position group")

    totals, players = _collect(db, as_of, competition_id)
    by_group = defaultdict(list)
    for (league, group, pid), total in totals.items():
        if position is not None and group != position:
            continue
        by_group[(league, group)].append((pid, total))
    grouped_results = []
    for (league, group), entries in sorted(by_group.items()):
        # Cohort includes adults to prevent a tiny U22 sample from defining
        # its own standard; only well-observed peers determine the reference.
        peers = [
            total["rating_sum"] / total["rated_minutes"]
            for _, total in entries
            if total["rated_minutes"] >= PEER_MINUTES
            and total["rated_minutes"] / total["minutes"] >= MIN_COVERAGE
        ]
        cohort_ready = len(peers) >= min_cohort
        mean = fmean(peers) if cohort_ready else None
        deviation = max(pstdev(peers), 0.35) if cohort_ready else None

        candidates = []
        for pid, total in entries:
            player = players[pid]
            if player_id is not None and pid != player_id:
                continue
            if player.birth_date is None:
                continue
            age = age_on(player.birth_date, as_of)
            if not 0 <= age < 22:
                continue
            minutes = total["minutes"]
            if category == "emerging" and not 30 <= minutes < min_minutes:
                continue
            if category == "established" and minutes < min_minutes:
                continue

            rated_minutes = total["rated_minutes"]
            rating_coverage = rated_minutes / minutes
            raw_rating = total["rating_sum"] / rated_minutes if rated_minutes else None
            score = None
            delta = None
            if cohort_ready and raw_rating is not None and rating_coverage >= MIN_COVERAGE:
                delta = (raw_rating - mean) / deviation
                bounded_delta = max(-2.5, min(2.5, delta))
                reliability = rated_minutes / (rated_minutes + SHRINK_MINUTES)
                score = round(max(0, min(100, 50 + 15 * bounded_delta * reliability)), 1)

            confidence = round(
                min(1.0, rated_minutes / 900) * rating_coverage, 3
            )
            features, metric_coverage = _features(total)
            signals = []
            if delta is not None and delta >= 1.0 and confidence >= 0.05:
                signals.append("rating_above_position_league_peers")
            if (
                group in {"attacker", "midfielder"}
                and features["goals_per90"] is not None
                and features["assists_per90"] is not None
                and features["goals_per90"] + features["assists_per90"] >= 0.8
            ):
                signals.append("early_goal_involvement")
            if rating_coverage < MIN_COVERAGE:
                signals.append("insufficient_rating_coverage")
            if not cohort_ready:
                signals.append("insufficient_comparable_peers")
            if minutes < min_minutes:
                signals.append("small_playing_sample")

            candidates.append({
                "player_id": pid,
                "name": player.name,
                "age": age,
                "competition_id": league,
                "position_group": group,
                "minutes": minutes,
                "matches": total["matches"],
                "rated_matches": total["rated_matches"],
                "average_rating": round(raw_rating, 2) if raw_rating is not None else None,
                "rating_coverage": round(rating_coverage, 3),
                "confidence": confidence,
                "confidence_is_calibrated_probability": False,
                "relative_score": score,
                "status": "comparable" if score is not None else (
                    "insufficient_peers" if not cohort_ready else "insufficient_rating_data"
                ),
                "features_per90": features,
                "feature_coverage": metric_coverage,
                "signals": signals,
                "explanation": (
                    "Relative to well-observed players in the same league and position; "
                    "rating advantage is shrunk toward cohort average for small samples."
                    if score is not None else
                    "No relative score: insufficient peer group or observed player ratings."
                ),
            })
        # Unranked rows are retained for review but never treated as high scores.
        candidates.sort(key=lambda row: (
            row["relative_score"] is None,
            -(row["relative_score"] if row["relative_score"] is not None else -1),
            -row["minutes"], row["player_id"],
        ))
        if candidates:
            grouped_results.append({
                "competition_id": league,
                "position_group": group,
                "peer_count": len(peers),
                "minimum_peer_count": min_cohort,
                "cohort_ready": cohort_ready,
                "cohort_mean_rating": round(mean, 3) if mean is not None else None,
                "cohort_sd_floor": round(deviation, 3) if deviation is not None else None,
                "players": candidates[:limit],
            })
    return {
        "as_of": as_of,
        "category": category,
        "model_version": MODEL_VERSION,
        "model_type": "transparent_heuristic_not_ai",
        "lookback_days": LOOKBACK_DAYS,
        "league_strength_adjusted": False,
        "cross_league_ranking": False,
        "relative_score_is_probability": False,
        "cohort_uses_all_ages": True,
        "groups": grouped_results,
    }
