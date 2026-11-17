"""Public match-only WebSocket stream with durable replay through REST."""
import asyncio

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
    inbound = None
    try:
        await pubsub.subscribe(f"footscore:match:{match_id}")
        # Subscribe before reading the DB snapshot: buffered Redis messages can
        # repeat data from the snapshot, but won't be silently missed.
        with SessionLocal() as db:
            snapshot = match_snapshot(db, match_id)
        await websocket.accept()
        await websocket.send_json(snapshot)
        inbound = asyncio.create_task(websocket.receive())
        while True:
            outbound = asyncio.create_task(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            )
            done, _ = await asyncio.wait(
                {inbound, outbound}, return_when=asyncio.FIRST_COMPLETED
            )
            if inbound in done:
                incoming = inbound.result()
                if incoming["type"] == "websocket.disconnect":
                    if not outbound.done():
                        outbound.cancel()
                        await asyncio.gather(outbound, return_exceptions=True)
                    break
                inbound = asyncio.create_task(websocket.receive())
            if outbound in done:
                message = outbound.result()
                if message and message.get("type") == "message":
                    await websocket.send_text(message["data"])
            else:
                outbound.cancel()
                await asyncio.gather(outbound, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
    finally:
        if inbound is not None and not inbound.done():
            inbound.cancel()
            await asyncio.gather(inbound, return_exceptions=True)
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
