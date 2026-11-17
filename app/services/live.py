"""Normalize and upsert API-Football live events and team match statistics."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchEvent, Player, Team, TeamMatchStat
from app.services.live_updates import record_update
from app.services.notifications import invalidate_event_notifications, notify_new_event


def event_kind(item: dict) -> str:
    kind = str(item.get("type") or "").lower()
    detail = str(item.get("detail") or "").lower()
    if kind == "goal":
        return "goal"
    if kind == "card":
        return "red_card" if "red" in detail else "yellow_card"
    if kind in {"subst", "substitution"}:
        return "substitution"
    if kind == "var":
        return "var"
    return kind or "other"


def event_identity(item: dict, occurrence: int) -> str:
    # Provider does not reliably supply an event ID. Identity excludes mutable
    # detail/comments/assister so their corrections update the existing row.
    # Time, player or team corrections can still change identity.
    fields = {
        "time": item.get("time"),
        "team_id": (item.get("team") or {}).get("id"),
        "player_id": (item.get("player") or {}).get("id"),
        "type": str(item.get("type") or "").lower(),
    }
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{digest}:{occurrence}"


def _ensure_player(db: Session, raw: dict | None) -> int | None:
    if not raw or raw.get("id") is None:
        return None
    pid = str(raw["id"])
    player = db.scalar(select(Player).where(
        Player.provider == "api-football", Player.provider_id == pid,
    ))
    if player is None:
        player = Player(
            provider="api-football", provider_id=pid,
            name=str(raw.get("name") or f"Player {pid}"),
        )
        db.add(player)
        db.flush()
    elif raw.get("name"):
        player.name = str(raw["name"])
    return player.id


def upsert_match_events(
    db: Session, match: Match, events: list[dict], *,
    reconcile: bool = False, allow_empty: bool = False,
) -> int:
    """Ingest a snapshot; only explicitly complete snapshots retract absent events.

    A transient empty provider response must not erase all previous events.
    Set allow_empty only if independently verified as a genuine empty snapshot.
    """
    seen: dict[str, int] = {}
    seen_ids: set[str] = set()
    changed_rows: list[MatchEvent] = []
    count = 0
    for item in events:
        base_id = event_identity(item, 0).split(":")[0]
        occurrence = seen.get(base_id, 0)
        seen[base_id] = occurrence + 1
        event_id = event_identity(item, occurrence)
        seen_ids.add(event_id)
        player_id = _ensure_player(db, item.get("player"))
        _ensure_player(db, item.get("assist"))
        row = db.scalar(select(MatchEvent).where(
            MatchEvent.provider == "api-football",
            MatchEvent.match_id == match.id,
            MatchEvent.provider_id == event_id,
        ))
        is_new = row is None
        if is_new:
            row = MatchEvent(
                provider="api-football", provider_id=event_id,
                match_id=match.id, kind=event_kind(item),
                is_active=True,
            )
            db.add(row)
        raw_time = item.get("time") or {}
        minute = raw_time.get("elapsed")
        kind = event_kind(item)
        changed = (
            is_new or not row.is_active or row.player_id != player_id
            or row.minute != minute or row.kind != kind or row.payload != item
        )
        row.player_id = player_id
        row.minute = minute
        row.kind = kind
        row.payload = item
        row.is_active = True
        row.retracted_at = None
        row.observed_at = datetime.now(timezone.utc)
        if changed:
            changed_rows.append(row)
        count += 1

    db.flush()
    for row in changed_rows:
        notify_new_event(db, match, row)
        record_update(db, match.id, "event_upsert", {
            "event_id": row.id, "kind": row.kind, "minute": row.minute,
            "player_id": row.player_id, "payload": row.payload,
        })

    if reconcile and (events or allow_empty):
        # Rows absent from an explicitly complete provider snapshot are
        # retracted, never deleted: clients need a correction history.
        active_rows = db.scalars(select(MatchEvent).where(
            MatchEvent.provider == "api-football",
            MatchEvent.match_id == match.id,
            MatchEvent.is_active.is_(True),
        )).all()
        for old in active_rows:
            if old.provider_id in seen_ids:
                continue
            old.is_active = False
            old.retracted_at = datetime.now(timezone.utc)
            invalidate_event_notifications(db, old)
            record_update(db, match.id, "event_retracted", {
                "event_id": old.id, "kind": old.kind,
            })
    db.flush()
    return count


def upsert_match_statistics(db: Session, match: Match, response: list[dict]) -> int:
    """Upsert one row per team/statistic name, preserving provider value types."""
    count = 0
    for entry in response:
        raw_team = entry.get("team") or {}
        if raw_team.get("id") is None:
            continue
        team = db.scalar(select(Team).where(
            Team.provider == "api-football",
            Team.provider_id == str(raw_team["id"]),
        ))
        if team is None:
            continue
        for statistic in entry.get("statistics") or []:
            kind = statistic.get("type")
            if not kind:
                continue
            row = db.scalar(select(TeamMatchStat).where(
                TeamMatchStat.match_id == match.id,
                TeamMatchStat.team_id == team.id,
                TeamMatchStat.name == kind,
            ))
            if row is None:
                row = TeamMatchStat(match_id=match.id, team_id=team.id, name=kind)
                db.add(row)
                changed = True
            else:
                changed = row.value != statistic.get("value")
            row.value = statistic.get("value")
            row.observed_at = datetime.now(timezone.utc)
            if changed:
                record_update(db, match.id, "statistic_upsert", {
                    "team_id": team.id, "name": kind, "value": row.value,
                })
            count += 1
    db.flush()
    return count
