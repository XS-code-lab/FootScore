"""Transparent initial U22 scouting leaderboard; not an ML/AI prediction."""
from collections import defaultdict
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Player, PlayerMatchStats

FINAL_STATUSES = {"FT", "AET", "PEN"}
PRIOR_MINUTES = 270
PRIOR_RATING = 6.5


def age_on(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def scouting_u22(
    db: Session, *,
    as_of: date | None = None,
    category: str = "established",
    position: str | None = None,
    limit: int = 50,
    min_minutes: int = 300,
) -> list[dict]:
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()
    if category not in {"established", "emerging"}:
        raise ValueError("Invalid scouting category")
    if min_minutes < 1:
        raise ValueError("min_minutes must be positive")

    end_time = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    stmt = (
        select(Player, PlayerMatchStats, Match)
        .join(PlayerMatchStats, PlayerMatchStats.player_id == Player.id)
        .join(Match, Match.id == PlayerMatchStats.match_id)
        .where(
            Player.birth_date.is_not(None),
            Match.status.in_(FINAL_STATUSES),
            Match.kickoff.is_not(None),
            Match.kickoff <= end_time,
        )
    )
    if position:
        stmt = stmt.where(Player.position == position)
    totals = defaultdict(lambda: {
        "minutes": 0, "matches": 0, "goals": 0, "assists": 0,
        "rating_minutes": 0, "weighted_rating": 0.0,
        "rated_matches": 0, "competition_ids": set(),
    })
    players: dict[int, Player] = {}
    for player, stats, match in db.execute(stmt):
        if player.birth_date is None or not 0 <= age_on(player.birth_date, as_of) < 22:
            continue
        if stats.minutes is None or stats.minutes <= 0:
            continue
        players[player.id] = player
        total = totals[player.id]
        total["matches"] += 1
        total["minutes"] += stats.minutes
        total["goals"] += stats.goals or 0
        total["assists"] += stats.assists or 0
        if match.competition_id is not None:
            total["competition_ids"].add(match.competition_id)
        if stats.rating is not None:
            total["rating_minutes"] += stats.minutes
            total["weighted_rating"] += stats.rating * stats.minutes
            total["rated_matches"] += 1

    results = []
    for player_id, total in totals.items():
        minutes = total["minutes"]
        if category == "established" and minutes < min_minutes:
            continue
        if category == "emerging" and not (30 <= minutes < min_minutes):
            continue
        # Avoid ranking a player when no provider match rating exists.
        if total["rating_minutes"] == 0:
            continue
        rated_minutes = total["rating_minutes"]
        raw_average = total["weighted_rating"] / rated_minutes
        adjusted = (
            total["weighted_rating"] + PRIOR_RATING * PRIOR_MINUTES
        ) / (rated_minutes + PRIOR_MINUTES)
        player = players[player_id]
        results.append({
            "player_id": player_id,
            "name": player.name,
            "age": age_on(player.birth_date, as_of),
            "position": player.position,
            "nationality": player.nationality,
            "minutes": minutes,
            "matches": total["matches"],
            "rated_matches": total["rated_matches"],
            "goals": total["goals"],
            "assists": total["assists"],
            "goals_per_90": round(total["goals"] * 90 / minutes, 2),
            "assists_per_90": round(total["assists"] * 90 / minutes, 2),
            "average_rating": round(raw_average, 2),
            "adjusted_rating": round(adjusted, 3),
            "rating_coverage": round(rated_minutes / minutes, 3),
            "competition_ids": sorted(total["competition_ids"]),
            "sample_notice": "low_sample" if minutes < min_minutes else "established",
            "method": "minute_weighted_provider_rating_with_270_minute_prior",
        })
    results.sort(key=lambda row: (-row["adjusted_rating"], -row["minutes"], row["player_id"]))
    return results[:limit]
