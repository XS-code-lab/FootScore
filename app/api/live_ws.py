"""Public match-only WebSocket stream via Redis pub/sub.

A snapshot is delivered at connect time; persistent updates are available
through /matches/{id}/updates for reconnect/resume.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.models import LiveUpdate, Match
from app.services.live_updates import match_snapshot
from app.settings import settings

router = APIRouter(prefix="/api/v1", tags=["live"])


@router.websocket("/live/matches/{match_id}")
async def live_match_socket(websocket: WebSocket, match_id: int) -> None:
    with SessionLocal() as db:
        if db.get(Match, match_id) is None:
            await websocket.close(code=1008)
            return
    if not settings.redis_url:
        await websocket.close(code=1013, reason="Live stream not configured")
        return
    redis = from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(f"footscore:match:{match_id}")
        # Redis is subscribed before reading snapshot, avoiding missed DB
        # changes while connecting (duplicates can occur and are keyed by id).
        with SessionLocal() as db:
            snapshot = match_snapshot(db, match_id)
        await websocket.accept()
        await websocket.send_json(snapshot)
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True,
                                               timeout=15.0)
            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])
            else:
                # Ping to detect a client disconnect even if Redis has no events.
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
    finally:
        await pubsub.aclose()
        await redis.aclose()


@router.get("/matches/{match_id}/updates")
def match_updates(
    match_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = db.scalars(
        select(LiveUpdate).where(
            LiveUpdate.match_id == match_id, LiveUpdate.id > after_id,
        ).order_by(LiveUpdate.id).limit(limit)
    ).all()
    return [
        {"id": row.id, "match_id": row.match_id, "kind": row.kind,
         "data": row.payload, "created_at": row.created_at}
        for row in rows
    ]
