"""Optional Firebase Cloud Messaging dispatcher.

This MVP uses one worker. Delivery is at least once: crashes after Firebase
accepts a push but before commit can cause duplicates. Production needs leased
job claims, exponential retry, invalid token cleanup and monitoring.
"""
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, PushDelivery, PushDevice


def firebase_sender(token: str, notification: Notification) -> None:
    try:
        import firebase_admin
        from firebase_admin import messaging
    except ImportError as exc:
        raise RuntimeError("Install the optional 'push' dependencies") from exc
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    data = {
        "notification_id": str(notification.id),
        "kind": notification.kind,
        "player_id": str(notification.player_id),
        "match_id": str(notification.match_id),
    }
    messaging.send(messaging.Message(
        token=token,
        data=data,
        notification=messaging.Notification(
            title="FootScore",
            body={
                "goal": "A favorite player scored a goal",
                "assist": "A favorite player assisted a goal",
                "final_rating": "A favorite player's final match rating is available",
            }.get(notification.kind, "Player activity"),
        ),
    ))


def dispatch_pending(
    db: Session, sender: Callable[[str, Notification], None] = firebase_sender,
    *, limit: int = 50, max_attempts: int = 3,
) -> dict[str, int]:
    sent = 0
    failed = 0
    # Run a single process. A lease-based concurrent dispatcher is future work.
    rows = db.scalars(
        select(PushDelivery)
        .where(
            PushDelivery.status == "pending",
            PushDelivery.attempts < max_attempts,
        )
        .order_by(PushDelivery.id)
        .limit(limit)
    ).all()
    for delivery in rows:
        notification = db.get(Notification, delivery.notification_id)
        device = db.get(PushDevice, delivery.device_id)
        if notification is None or device is None:
            delivery.status = "failed"
            delivery.last_error = "Missing notification or device"
            failed += 1
            continue
        delivery.attempts += 1
        try:
            sender(device.registration_token, notification)
        except Exception as exc:
            # Do not leak registration tokens or credentials into logs.
            delivery.last_error = type(exc).__name__
            if delivery.attempts >= max_attempts:
                delivery.status = "failed"
            failed += 1
        else:
            delivery.status = "sent"
            delivery.sent_at = datetime.now(timezone.utc)
            delivery.last_error = None
            sent += 1
    db.flush()
    return {"sent": sent, "failed": failed}
