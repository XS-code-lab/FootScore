"""Cohort-aware U22 talent discovery and individual player scouting reports."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Competition, Player
from app.services.scouting_discovery import discover_u22

router = APIRouter(prefix="/api/v1/scouting/u22", tags=["scouting"])


@router.get("/discovery")
def u22_discovery(
    as_of: date | None = None,
    category: str = Query(default="emerging", pattern="^(emerging|established)$"),
    min_minutes: int = Query(default=300, ge=60, le=5000),
    min_cohort: int = Query(default=5, ge=3, le=100),
    competition_id: int | None = Query(default=None, ge=1),
    position: str | None = Query(
        default=None, pattern="^(attacker|midfielder|defender|goalkeeper)$",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    if competition_id is not None and db.get(Competition, competition_id) is None:
        raise HTTPException(status_code=404, detail="Competition not found")
    return discover_u22(
        db, as_of=as_of, category=category, min_minutes=min_minutes,
        min_cohort=min_cohort, competition_id=competition_id,
        position=position, limit=limit,
    )


@router.get("/reports/{player_id}")
def scouting_report(
    player_id: int,
    as_of: date | None = None,
    competition_id: int | None = Query(default=None, ge=1),
    min_cohort: int = Query(default=5, ge=3, le=100),
    db: Session = Depends(get_db),
) -> dict:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    if competition_id is not None and db.get(Competition, competition_id) is None:
        raise HTTPException(status_code=404, detail="Competition not found")
    emerging = discover_u22(
        db, as_of=as_of, category="emerging", min_cohort=min_cohort,
        competition_id=competition_id, player_id=player_id,
    )
    established = discover_u22(
        db, as_of=as_of, category="established", min_cohort=min_cohort,
        competition_id=competition_id, player_id=player_id,
    )
    reports = [
        item for result in (emerging, established)
        for group in result["groups"] for item in group["players"]
    ]
    return {
        "player_id": player_id,
        "name": player.name,
        "date_of_birth_verified": player.birth_date is not None,
        "model_version": emerging["model_version"],
        "model_type": emerging["model_type"],
        "cross_league_ranking": False,
        "reports": reports,
        "notice": (
            "No eligible completed-match evidence or verified U22 date of birth."
            if not reports else
            "Observed peer-relative performance, not a forecast of future potential."
        ),
    }
