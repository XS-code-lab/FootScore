from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Competition, Match, MatchEvent, TeamMatchStat

router = APIRouter(prefix="/api/v1", tags=["football"])


@router.get("/competitions")
def competitions(db: Session = Depends(get_db)) -> list[dict]:
    records = db.scalars(select(Competition).order_by(Competition.name).limit(200)).all()
    return [{"id": row.id, "name": row.name, "country": row.country,
             "provider": row.provider} for row in records]


@router.get("/matches")
def matches(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(Match).order_by(Match.kickoff, Match.id).limit(limit)
    if status:
        statement = statement.where(Match.status == status)
    rows = db.scalars(statement).all()
    return [{
        "id": item.id, "provider_id": item.provider_id,
        "competition_id": item.competition_id, "home_team_id": item.home_team_id,
        "away_team_id": item.away_team_id, "kickoff": item.kickoff,
        "status": item.status, "home_score": item.home_score,
        "away_score": item.away_score, "observed_at": item.observed_at,
    } for item in rows]


@router.get("/matches/{match_id}")
def match_detail(match_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(Match, match_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return {
        "id": item.id, "status": item.status, "home_score": item.home_score,
        "away_score": item.away_score, "kickoff": item.kickoff,
        "provider": item.provider, "provider_id": item.provider_id,
        "observed_at": item.observed_at,
    }


@router.get("/matches/{match_id}/events")
def match_events(match_id: int, db: Session = Depends(get_db)) -> list[dict]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = db.scalars(
        select(MatchEvent).where(MatchEvent.match_id == match_id,
                                 MatchEvent.is_active.is_(True))
        .order_by(MatchEvent.minute, MatchEvent.id)
    ).all()
    return [
        {"id": row.id, "kind": row.kind, "minute": row.minute,
         "player_id": row.player_id, "payload": row.payload,
         "observed_at": row.observed_at}
        for row in rows
    ]


@router.get("/matches/{match_id}/statistics")
def match_statistics(match_id: int, db: Session = Depends(get_db)) -> list[dict]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = db.scalars(
        select(TeamMatchStat).where(TeamMatchStat.match_id == match_id)
        .order_by(TeamMatchStat.team_id, TeamMatchStat.name)
    ).all()
    return [
        {"team_id": row.team_id, "name": row.name, "value": row.value,
         "observed_at": row.observed_at}
        for row in rows
    ]
