"""Normalize and upsert API-Football live events and team match statistics."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchEvent, Player, Team, TeamMatchStat
from app.services.notifications import notify_new_event


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
    # API-Football fixture events do not consistently include a stable event ID.
    # A deterministic fingerprint + duplicate occurrence avoids inserting a
    # duplicate event on repeated complete snapshots. Material corrections
    # produce a new identity; reconciliation is a later milestone.
    fields = {
        "time": item.get("time"),
        "team_id": (item.get("team") or {}).get("id"),
        "player_id": (item.get("player") or {}).get("id"),
        "assist_id": (item.get("assist") or {}).get("id"),
        "type": item.get("type"),
        "detail": item.get("detail"),
        "comments": item.get("comments"),
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


def upsert_match_events(db: Session, match: Match, events: list[dict]) -> int:
    """Idempotently merge one complete event snapshot for an existing match."""
    seen: dict[str, int] = {}
    new_events: list[MatchEvent] = []
    count = 0
    for item in events:
        base_id = event_identity(item, 0).split(":")[0]
        occurrence = seen.get(base_id, 0)
        seen[base_id] = occurrence + 1
        event_id = event_identity(item, occurrence)
        player_id = _ensure_player(db, item.get("player"))
        _ensure_player(db, item.get("assist"))
        row = db.scalar(select(MatchEvent).where(
            MatchEvent.provider == "api-football",
            MatchEvent.match_id == match.id,
            MatchEvent.provider_id == event_id,
        ))
        if row is None:
            row = MatchEvent(
                provider="api-football", provider_id=event_id,
                match_id=match.id, kind=event_kind(item),
            )
            db.add(row)
            new_events.append(row)
        row.player_id = player_id
        raw_time = item.get("time") or {}
        row.minute = raw_time.get("elapsed")
        row.kind = event_kind(item)
        row.payload = item
        row.observed_at = datetime.now(timezone.utc)
        count += 1
    db.flush()
    for new_event in new_events:
        notify_new_event(db, match, new_event)
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
            row.value = statistic.get("value")
            row.observed_at = datetime.now(timezone.utc)
            count += 1
    db.flush()
    return count
