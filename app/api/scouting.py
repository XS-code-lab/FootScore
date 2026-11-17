"""Public read-only player statistics and preliminary U22 scouting routes."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Match, Player, PlayerMatchStats
from app.services.scouting import scouting_u22

router = APIRouter(prefix="/api/v1", tags=["scouting"])


@router.get("/matches/{match_id}/players")
def match_players(match_id: int, db: Session = Depends(get_db)) -> dict:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    stmt = (
        select(PlayerMatchStats, Player)
        .join(Player, Player.id == PlayerMatchStats.player_id)
        .where(PlayerMatchStats.match_id == match_id)
        .order_by(Player.name, Player.id)
    )
    entries = [
        {
            "player_id": player.id,
            "name": player.name,
            "team_id": stat.team_id,
            "position": player.position,
            "minutes": stat.minutes,
            "goals": stat.goals,
            "assists": stat.assists,
            "rating": stat.rating,
            "observed_at": stat.updated_at,
        }
        for stat, player in db.execute(stmt)
    ]
    return {
        "match_id": match_id,
        "match_status": match.status,
        "rating_is_final": match.status in {"FT", "AET", "PEN"},
        "players": entries,
    }


@router.get("/players/{player_id}")
def player_profile(player_id: int, db: Session = Depends(get_db)) -> dict:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return {
        "id": player.id, "name": player.name,
        "birth_date": player.birth_date, "nationality": player.nationality,
        "position": player.position, "provider": player.provider,
        "updated_at": player.updated_at,
    }


@router.get("/scouting/u22")
def u22_leaderboard(
    category: str = Query(default="established", pattern="^(established|emerging)$"),
    as_of: date | None = None,
    position: str | None = Query(default=None, max_length=50),
    min_minutes: int = Query(default=300, ge=60, le=5000),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "category": category,
        "as_of": as_of or date.today(),
        "min_minutes": min_minutes,
        "scope": "completed_fixtures_with_available_birth_dates_and_provider_ratings",
        "league_adjusted": False,
        "model_type": "heuristic_not_ai",
        "players": scouting_u22(
            db, category=category, as_of=as_of,
            position=position, limit=limit, min_minutes=min_minutes,
        ),
    }
