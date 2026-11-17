"""Idempotent player alerts with correction-aware invalidation."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    FavoritePlayer, Match, MatchEvent, Notification,
    NotificationPreference, Player, PlayerMatchStats, PushDelivery, PushDevice, User,
)

FINAL_STATUSES = {"FT", "AET", "PEN"}


def _subscribers(db: Session, player_id: int, kind: str, rating: float | None = None):
    favorites = db.scalars(
        select(FavoritePlayer).join(User, User.id == FavoritePlayer.user_id)
        .where(FavoritePlayer.player_id == player_id, User.is_active.is_(True))
    ).all()
    for favorite in favorites:
        pref = db.scalar(select(NotificationPreference).where(
            NotificationPreference.user_id == favorite.user_id,
        ))
        if pref is not None:
            key = {"goal": "goals", "assist": "assists", "final_rating": "final_rating"}[kind]
            if not getattr(pref, key):
                continue
            if (kind == "final_rating" and pref.min_final_rating is not None
                    and (rating is None or rating < pref.min_final_rating)):
                continue
        yield favorite.user_id


def _enqueue(
    db: Session, *, user_id: int, match_id: int, player_id: int,
    kind: str, event_key: str, payload: dict, source_event_id: int | None = None,
) -> bool:
    existing = db.scalar(select(Notification).where(
        Notification.user_id == user_id,
        Notification.event_key == event_key,
    ))
    if existing is not None:
        if existing.invalidated_at is not None:
            existing.invalidated_at = None
            existing.payload = payload
            for delivery in db.scalars(
                select(PushDelivery).where(PushDelivery.notification_id == existing.id)
            ):
                if delivery.status == "canceled" and delivery.sent_at is None:
                    delivery.status = "pending"
        return False
    notification = Notification(
        user_id=user_id, player_id=player_id, match_id=match_id,
        event_key=event_key, kind=kind, payload=payload,
        source_event_id=source_event_id,
    )
    db.add(notification)
    db.flush()
    for device_id in db.scalars(select(PushDevice.id).where(
        PushDevice.user_id == user_id,
    )):
        db.add(PushDelivery(
            notification_id=notification.id, device_id=device_id, status="pending",
        ))
    return True


def _fanout(
    db: Session, match: Match, player_id: int, kind: str,
    event_key: str, data: dict, rating: float | None = None,
    source_event_id: int | None = None,
) -> int:
    count = 0
    for user_id in _subscribers(db, player_id, kind, rating):
        if _enqueue(
            db, user_id=user_id, match_id=match.id, player_id=player_id,
            kind=kind, event_key=event_key, payload=data,
            source_event_id=source_event_id,
        ):
            count += 1
    return count


def _invalidate(db: Session, note: Notification) -> None:
    if note.invalidated_at is not None:
        return
    note.invalidated_at = datetime.now(timezone.utc)
    for delivery in db.scalars(
        select(PushDelivery).where(
            PushDelivery.notification_id == note.id,
            PushDelivery.status == "pending",
        )
    ):
        delivery.status = "canceled"


def invalidate_event_notifications(db: Session, event: MatchEvent) -> int:
    """Suppress pending pushes and mark old inbox notifications as corrected."""
    count = 0
    rows = db.scalars(select(Notification).where(
        Notification.source_event_id == event.id,
        Notification.invalidated_at.is_(None),
    )).all()
    for note in rows:
        _invalidate(db, note)
        count += 1
    return count


def notify_new_event(db: Session, match: Match, event: MatchEvent) -> int:
    """Reconcile desired scorer/assister notifications for an active goal event."""
    if not event.is_active:
        invalidate_event_notifications(db, event)
        return 0
    raw = event.payload or {}
    detail = str(raw.get("detail") or "").lower()
    desired: list[tuple[str, int]] = []
    if (event.kind == "goal" and not any(
        flag in detail for flag in ("own goal", "missed", "cancel", "disallow")
    )):
        if event.player_id is not None:
            desired.append(("goal", event.player_id))
        assist_id = (raw.get("assist") or {}).get("id")
        if assist_id is not None:
            assister = db.scalar(select(Player).where(
                Player.provider == event.provider,
                Player.provider_id == str(assist_id),
            ))
            if assister is not None and assister.id != event.player_id:
                desired.append(("assist", assister.id))

    active = set(desired)
    previous = db.scalars(select(Notification).where(
        Notification.source_event_id == event.id,
        Notification.invalidated_at.is_(None),
    )).all()
    for note in previous:
        if (note.kind, note.player_id) not in active:
            _invalidate(db, note)

    count = 0
    for kind, player_id in desired:
        count += _fanout(
            db, match, player_id, kind,
            f"event:{match.id}:{event.provider_id}:{kind}:{player_id}",
            {"minute": event.minute, "detail": raw.get("detail")},
            source_event_id=event.id,
        )
    return count


def notify_final_ratings(db: Session, match: Match) -> int:
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
