"""Durable match update stream; Redis publishes after database commit."""
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LiveUpdate, Match, MatchEvent, PlayerMatchStats, TeamMatchStat


def record_update(db: Session, match_id: int, kind: str, payload: dict) -> None:
    """Append a public-data-only update inside the same ingestion transaction."""
    db.add(LiveUpdate(match_id=match_id, kind=kind, payload=payload))


def latest_id(db: Session, match_id: int) -> int:
    return db.scalar(
        select(func.max(LiveUpdate.id)).where(LiveUpdate.match_id == match_id)
    ) or 0


def match_snapshot(db: Session, match_id: int) -> dict | None:
    match = db.get(Match, match_id)
    if match is None:
        return None
    events = db.scalars(
        select(MatchEvent)
        .where(MatchEvent.match_id == match_id, MatchEvent.is_active.is_(True))
        .order_by(MatchEvent.minute, MatchEvent.id)
    ).all()
    team_stats = db.scalars(
        select(TeamMatchStat).where(TeamMatchStat.match_id == match_id)
        .order_by(TeamMatchStat.team_id, TeamMatchStat.name)
    ).all()
    player_stats = db.scalars(
        select(PlayerMatchStats).where(PlayerMatchStats.match_id == match_id)
        .order_by(PlayerMatchStats.player_id)
    ).all()
    return {
        "type": "snapshot",
        "match_id": match.id,
        "last_update_id": latest_id(db, match_id),
        "status": match.status,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "observed_at": match.observed_at.isoformat() if match.observed_at else None,
        "events": [
            {"id": event.id, "minute": event.minute, "kind": event.kind,
             "player_id": event.player_id, "payload": event.payload}
            for event in events
        ],
        "statistics": [
            {"team_id": row.team_id, "name": row.name, "value": row.value}
            for row in team_stats
        ],
        "players": [
            {"player_id": row.player_id, "team_id": row.team_id,
             "minutes": row.minutes, "goals": row.goals, "assists": row.assists,
             "rating": row.rating}
            for row in player_stats
        ],
    }


def drain_outbox(db: Session, redis_client, *, limit: int = 100) -> int:
    """Publish under PostgreSQL row locks; use REST replay for missed messages.

    Multiple PostgreSQL workers skip rows already reserved by another worker.
    Redis publish and DB commit are not atomic: a process crash after publish
    can duplicate a message. Clients must deduplicate by update id.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("Outbox batch size must be between 1 and 1000")
    query = (
        select(LiveUpdate)
        .where(LiveUpdate.published_at.is_(None))
        .order_by(LiveUpdate.id)
        .limit(limit)
    )
    if db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = db.scalars(query).all()
    published = 0
    for update in rows:
        message = {
            "id": update.id,
            "match_id": update.match_id,
            "kind": update.kind,
            "data": update.payload,
        }
        redis_client.publish(
            f"footscore:match:{update.match_id}",
            json.dumps(message, separators=(",", ":"), default=str),
        )
        update.published_at = datetime.now(timezone.utc)
        published += 1
    db.flush()
    return published
