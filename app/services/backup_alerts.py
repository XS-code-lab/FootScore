"""Opt-in, database-deduplicated backup-health webhook notifications.

The webhook contains NO bucket, S3 keys, email addresses, tokens or secrets.
A recent object means *visible*, not authentic or recoverable.
"""
import hashlib
import ipaddress
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import BackupAlertState

VALID_CONDITIONS = {"recent_unverified", "missing", "stale", "error"}
ALARM_EVENTS = {
    "missing": "backup_missing",
    "stale": "backup_stale",
    "error": "backup_inventory_unavailable",
}
RECOVERY_EVENT = "backup_visibility_restored"


def validate_webhook_url(url: str) -> None:
    """Only trust operator-configured, public-domain HTTPS endpoints on 443."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid backup alert webhook URL") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https" or not host
        or parsed.username is not None or parsed.password is not None
        or parsed.fragment or port not in (None, 443)
        or host == "localhost" or host.endswith((".local", ".internal", ".localhost"))
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Backup alert webhook must be a public HTTPS URL on port 443")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Literal IP addresses are not valid alert webhook targets")
    # Operator-configured URL only. DNS rebinding and trusted-host approval
    # still require network-layer egress policies in production.


def send_health_webhook(
    payload: dict, *, url: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """One attempt only; HTTP errors are sanitized and never counted as sent."""
    url = url or os.environ.get("BACKUP_ALERT_WEBHOOK_URL", "")
    validate_webhook_url(url)
    try:
        with httpx.Client(
            timeout=5.0, trust_env=False, follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.post(url, json=payload)
        if not 200 <= response.status_code < 300:
            raise RuntimeError("Backup webhook returned non-success status")
    except httpx.HTTPError as exc:
        # Never include exception text: webhook URLs can contain credentials.
        raise RuntimeError("Backup webhook delivery failed") from exc


def scope_digest(bucket: str, prefix: str) -> str:
    return hashlib.sha256(f"{bucket}\0{prefix}".encode("utf-8")).hexdigest()


def _aware(now: datetime) -> datetime:
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else (
        now.astimezone(timezone.utc)
    )


def proposed_event(
    old_condition: str, new_condition: str, *,
    notified_at: datetime | None, now: datetime, remind_hours: int = 24,
) -> str | None:
    """Pure transition function, including bounded daily reminders."""
    if old_condition not in VALID_CONDITIONS | {"unknown"}:
        raise ValueError("Invalid previous backup condition")
    if new_condition not in VALID_CONDITIONS:
        raise ValueError("Invalid backup condition")
    if not 1 <= remind_hours <= 168:
        raise ValueError("Reminder window must be between 1 and 168 hours")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Alert time must be timezone-aware")
    if new_condition == "recent_unverified":
        if old_condition in ALARM_EVENTS and notified_at is not None:
            return RECOVERY_EVENT
        return None
    if old_condition != new_condition or notified_at is None:
        return ALARM_EVENTS[new_condition]
    elapsed = (now - _aware(notified_at)).total_seconds()
    if elapsed >= remind_hours * 3600:
        return ALARM_EVENTS[new_condition]
    return None


def deliver_backup_alert(
    db: Session, *, bucket: str, prefix: str, condition: str,
    sender=send_health_webhook, at: datetime | None = None,
    remind_hours: int = 24,
) -> dict:
    """Send under one SQL row lock; caller MUST commit/rollback its transaction.

    Concurrent PostgreSQL workers reserve the same scope row. If webhook
    fails, the caller rolls back and next run can try again. A crash after
    remote delivery before commit may duplicate an alert (at-least-once).
    """
    if condition not in VALID_CONDITIONS:
        raise ValueError("Invalid backup health condition")
    if not 1 <= remind_hours <= 168:
        raise ValueError("Invalid backup alert reminder window")
    if at is not None and (at.tzinfo is None or at.utcoffset() is None):
        raise ValueError("Alert time must be timezone-aware")
    digest = scope_digest(bucket, prefix)
    dialect = db.bind.dialect.name
    row_values = {
        "scope_hash": digest, "condition": "unknown",
        "updated_at": at or datetime.now(timezone.utc),
    }
    if dialect == "postgresql":
        stmt = pg_insert(BackupAlertState).values(**row_values).on_conflict_do_nothing(
            index_elements=["scope_hash"],
        )
    elif dialect == "sqlite":
        stmt = sqlite_insert(BackupAlertState).values(**row_values).on_conflict_do_nothing(
            index_elements=["scope_hash"],
        )
    else:
        raise RuntimeError("Backup webhook coordination requires PostgreSQL or SQLite")
    db.execute(stmt)
    row = db.scalar(select(BackupAlertState).where(
        BackupAlertState.scope_hash == digest,
    ).with_for_update())
    if row is None:
        raise RuntimeError("Backup alert state was not created")
    if at is None:
        # PostgreSQL NOW() is transaction-start time; use the real clock
        # AFTER waiting for the row lock to prevent stale comparisons.
        at = (
            db.scalar(select(func.clock_timestamp()))
            if dialect == "postgresql" else datetime.now(timezone.utc)
        )
    at = _aware(at)
    event = proposed_event(
        row.condition, condition, notified_at=row.notified_at,
        now=at, remind_hours=remind_hours,
    )
    if event:
        payload = {
            "service": "FootScore", "event": event,
            "severity": "info" if event == RECOVERY_EVENT else "warning",
            "observed_at": at.isoformat(),
            "cryptographic_integrity_verified": False,
            "restore_verified": False,
        }
        # Do not mark a notification as delivered until sender returns.
        sender(payload)
        row.notified_at = at
    elif condition == "recent_unverified" and row.condition != condition:
        row.notified_at = None
    row.condition = condition
    row.updated_at = at
    db.flush()
    return {
        "state": condition,
        "event": event,
        "webhook_sent": bool(event),
        "scope": "hashed_in_database",
    }
