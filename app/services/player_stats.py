"""Player fixture statistics and profile enrichment (API-Football v3)."""
from datetime import date, datetime, timezone
from math import isfinite

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Player, PlayerMatchStats, Team
from app.services.live_updates import record_update
from app.services.notifications import notify_final_ratings


def parse_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_rating(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number) or not 0 <= number <= 10:
        return None
    return round(number, 2)


def _player(db: Session, info: dict) -> Player | None:
    external_id = info.get("id")
    if external_id is None:
        return None
    pid = str(external_id)
    player = db.scalar(select(Player).where(
        Player.provider == "api-football", Player.provider_id == pid,
    ))
    if player is None:
        player = Player(
            provider="api-football", provider_id=pid,
            name=str(info.get("name") or f"Player {pid}"),
        )
        db.add(player)
        db.flush()
    elif info.get("name"):
        player.name = str(info["name"])
    return player


def upsert_fixture_players(db: Session, match: Match, response: list[dict]) -> int:
    """Upsert per-player fixture stats, keeping unknown values null.

    API-Football returns one block per team with a players[] array.
    Ratings may change during the match; match status defines finality.
    """
    updated = 0
    for team_block in response:
        external_team = (team_block.get("team") or {}).get("id")
        if external_team is None:
            continue
        team = db.scalar(select(Team).where(
            Team.provider == "api-football",
            Team.provider_id == str(external_team),
        ))
        if team is None or team.id not in {match.home_team_id, match.away_team_id}:
            continue
        for item in team_block.get("players") or []:
            stats = item.get("statistics") or []
            if not stats or not isinstance(stats[0], dict):
                continue
            player = _player(db, item.get("player") or {})
            if player is None:
                continue
            row = db.scalar(select(PlayerMatchStats).where(
                PlayerMatchStats.match_id == match.id,
                PlayerMatchStats.player_id == player.id,
            ))
            is_new = row is None
            if is_new:
                row = PlayerMatchStats(match_id=match.id, player_id=player.id)
                db.add(row)
            previous = (
                row.minutes, row.goals, row.assists, row.rating, row.team_id,
            ) if not is_new else None
            detail = stats[0]
            games = detail.get("games") or {}
            goals = detail.get("goals") or {}
            row.team_id = team.id
            row.minutes = parse_nonnegative_int(games.get("minutes"))
            row.goals = parse_nonnegative_int(goals.get("total"))
            row.assists = parse_nonnegative_int(goals.get("assists"))
            row.rating = parse_rating(games.get("rating"))
            row.payload = detail
            row.updated_at = datetime.now(timezone.utc)
            current = (row.minutes, row.goals, row.assists, row.rating, row.team_id)
            if is_new or previous != current:
                record_update(db, match.id, "player_stat_changed", {
                    "player_id": player.id, "team_id": team.id,
                    "minutes": row.minutes, "goals": row.goals,
                    "assists": row.assists, "rating": row.rating,
                })
            if games.get("position"):
                player.position = str(games["position"])[:50]
            updated += 1
    db.flush()
    if updated:
        notify_final_ratings(db, match)
    return updated


def upsert_player_profile(db: Session, response: list[dict], provider_id: int) -> Player | None:
    """Enrich a player with verified birth date; never infer it from a supplied age."""
    for entry in response:
        info = entry.get("player") or {}
        if str(info.get("id")) != str(provider_id):
            continue
        player = _player(db, info)
        if player is None:
            continue
        birth = (info.get("birth") or {}).get("date")
        if birth:
            try:
                player.birth_date = date.fromisoformat(str(birth))
            except ValueError:
                pass
        if info.get("nationality"):
            player.nationality = str(info["nationality"])[:100]
        stats = entry.get("statistics") or []
        if stats and (stats[0].get("games") or {}).get("position"):
            player.position = str(stats[0]["games"]["position"])[:50]
        player.updated_at = datetime.now(timezone.utc)
        db.flush()
        return player
    return None
