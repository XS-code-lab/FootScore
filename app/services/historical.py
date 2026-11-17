"""Quota-aware historical player fixture backfill planning and local coverage audit.

Provider responses are observations, not guarantees of complete coverage.
The planner is offline and never makes HTTP calls.
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HistoricalPlayerFetch, Match

FINAL_STATUSES = frozenset(("FT", "AET", "PEN"))
MAX_RANGE_DAYS = 366


def validate_window(start: date, end: date) -> None:
    if start > end or (end - start).days > MAX_RANGE_DAYS:
        raise ValueError("Choose an ordered historical window of at most 367 days")
    if end > datetime.now(timezone.utc).date():
        raise ValueError("Historical backfill dates cannot include future days")


def _fixtures(db: Session, competition_id: int, start: date, end: date):
    validate_window(start, end)
    if competition_id < 1:
        raise ValueError("competition_id must be positive")
    from_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    until_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return db.scalars(
        select(Match).where(
            Match.competition_id == competition_id,
            Match.provider == "api-football",
            Match.status.in_(FINAL_STATUSES),
            Match.kickoff.is_not(None),
            Match.kickoff >= from_dt,
            Match.kickoff < until_dt,
        ).order_by(Match.kickoff, Match.id)
    ).all()


def audit_fixture_player_coverage(
    db: Session, *, competition_id: int, start: date, end: date,
) -> dict:
    """Report only database-observed coverage; never assert season completeness."""
    fixtures = _fixtures(db, competition_id, start, end)
    local_ids = [item.id for item in fixtures]
    receipts = db.scalars(
        select(HistoricalPlayerFetch).where(
            HistoricalPlayerFetch.match_id.in_(local_ids)
        )
    ).all() if local_ids else []
    by_id = {receipt.match_id: receipt for receipt in receipts}
    counts = {"imported": 0, "empty": 0, "not_fetched": 0}
    rows = []
    for match in fixtures:
        receipt = by_id.get(match.id)
        state = receipt.status if receipt else "not_fetched"
        counts[state] += 1
        rows.append({
            "match_id": match.id,
            "provider_fixture_id": match.provider_id,
            "kickoff": match.kickoff.isoformat() if match.kickoff else None,
            "status": match.status,
            "fetch_status": state,
            "players_imported": receipt.players_imported if receipt else None,
            "last_fetched_at": (
                receipt.fetched_at.isoformat() if receipt else None
            ),
        })
    return {
        "competition_id": competition_id,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "finished_fixtures_in_local_db": len(fixtures),
        "local_player_fetches": counts,
        "local_fixture_enumeration_externally_verified": False,
        "player_statistics_coverage_externally_verified": False,
        "is_suitable_for_negative_ml_labels": False,
        "caveat": (
            "Only locally known completed fixtures are counted. Missing fixtures "
            "and provider/player-stat omissions cannot be detected without an "
            "independent, licensed season coverage audit."
        ),
        "fixtures": rows,
    }


def plan_historical_backfill(
    db: Session, *, competition_id: int, start: date, end: date,
    max_requests: int = 5, retry_empty: bool = False,
) -> dict:
    """Select a bounded subset, without reserving quota or doing HTTP."""
    if not 1 <= max_requests <= 10:
        raise ValueError("max_requests must be between 1 and 10")
    report = audit_fixture_player_coverage(
        db, competition_id=competition_id, start=start, end=end,
    )
    statuses = {"not_fetched"}
    if retry_empty:
        statuses.add("empty")
    pending = [
        row for row in report["fixtures"] if row["fetch_status"] in statuses
    ]
    selected = pending[:max_requests]
    return {
        "competition_id": competition_id,
        "window_start": report["window_start"],
        "window_end": report["window_end"],
        "pending_count": len(pending),
        "estimated_api_calls": len(selected),
        "max_requests": max_requests,
        "retry_empty": retry_empty,
        "dry_run": True,
        "provider_budget_is_not_reserved_by_plan": True,
        "selected": [
            {"match_id": row["match_id"],
             "provider_fixture_id": row["provider_fixture_id"],
             "previous_fetch_status": row["fetch_status"]}
            for row in selected
        ],
        "notice": (
            "Plan only; execution requires API_FOOTBALL_KEY and --execute. "
            "Each fixture consumes one nonrefundable provider quota reservation."
        ),
    }
