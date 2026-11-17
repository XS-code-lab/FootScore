"""Create idempotent in-app alerts and optional push delivery records."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    FavoritePlayer, Match, MatchEvent, Notification,
    NotificationPreference, Player, PlayerMatchStats, PushDelivery, PushDevice, User,
)

FINAL_STATUSES = {"FT", "AET", "PEN"}


def _subscribers(db: Session, player_id: int, kind: str, rating: float | None = None):
    favorites = db.scalars(select(FavoritePlayer).join(
        User, User.id == FavoritePlayer.user_id,
    ).where(FavoritePlayer.player_id == player_id, User.is_active.is_(True))).all()
    for favorite in favorites:
        pref = db.scalar(select(NotificationPreference).where(
            NotificationPreference.user_id == favorite.user_id,
        ))
        if pref is not None:
            if not getattr(pref, {"goal": "goals", "assist": "assists",
                                  "final_rating": "final_rating"}[kind]):
                continue
            if (kind == "final_rating" and pref.min_final_rating is not None
                    and (rating is None or rating < pref.min_final_rating)):
                continue
        yield favorite.user_id


def _enqueue(
    db: Session, *, user_id: int, match_id: int, player_id: int,
    kind: str, event_key: str, payload: dict,
) -> bool:
    existing = db.scalar(select(Notification.id).where(
        Notification.user_id == user_id,
        Notification.event_key == event_key,
    ))
    if existing is not None:
        return False
    notification = Notification(
        user_id=user_id, player_id=player_id, match_id=match_id,
        event_key=event_key, kind=kind, payload=payload,
    )
    db.add(notification)
    db.flush()
    for device_id in db.scalars(
        select(PushDevice.id).where(PushDevice.user_id == user_id)
    ):
        db.add(PushDelivery(
            notification_id=notification.id, device_id=device_id, status="pending",
        ))
    return True


def _fanout(
    db: Session, match: Match, player_id: int, kind: str,
    event_key: str, data: dict, rating: float | None = None,
) -> int:
    count = 0
    for user_id in _subscribers(db, player_id, kind, rating):
        if _enqueue(
            db, user_id=user_id, match_id=match.id, player_id=player_id,
            kind=kind, event_key=event_key, payload=data,
        ):
            count += 1
    return count


def notify_new_event(db: Session, match: Match, event: MatchEvent) -> int:
    """Only newly detected positive goals and their assists generate alerts.

    Provider event fingerprints are best-effort. Reversals and corrections need
    a future reconciliation system before guaranteeing push correctness.
    """
    if event.kind != "goal":
        return 0
    raw = event.payload or {}
    detail = str(raw.get("detail") or "").lower()
    if "own goal" in detail or "missed" in detail or "cancel" in detail:
        return 0
    count = 0
    if event.player_id is not None:
        count += _fanout(
            db, match, event.player_id, "goal",
            f"event:{match.id}:{event.provider_id}:goal:{event.player_id}",
            {"minute": event.minute, "detail": raw.get("detail")},
        )
    assist_id = (raw.get("assist") or {}).get("id")
    if assist_id is not None:
        assister = db.scalar(select(Player).where(
            Player.provider == event.provider,
            Player.provider_id == str(assist_id),
        ))
        if assister is not None and assister.id != event.player_id:
            count += _fanout(
                db, match, assister.id, "assist",
                f"event:{match.id}:{event.provider_id}:assist:{assister.id}",
                {"minute": event.minute, "detail": raw.get("detail")},
            )
    return count


def notify_final_ratings(db: Session, match: Match) -> int:
    """One final-rating notification per match/player, regardless of reimports."""
    if match.status not in FINAL_STATUSES:
        return 0
    count = 0
    rows = db.scalars(select(PlayerMatchStats).where(
        PlayerMatchStats.match_id == match.id,
        PlayerMatchStats.rating.is_not(None),
    )).all()
    for stats in rows:
        count += _fanout(
            db, match, stats.player_id, "final_rating",
            f"final-rating:{match.id}:{stats.player_id}",
            {"rating": stats.rating, "minutes": stats.minutes}, rating=stats.rating,
        )
    return count
