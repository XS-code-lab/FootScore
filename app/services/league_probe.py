"""Advisory provider league coverage metadata, NOT season completeness."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import LeagueCoverageProbe
from app.providers.api_football import ApiFootballClient
from app.services.provider_guardrails import reserve_provider_call
from app.settings import settings

FEATURES = (
    "events", "statistics_fixtures", "statistics_players", "lineups",
)


def parse_league_coverage(response: list[dict], league: int, season: int) -> dict:
    for item in response:
        if str((item.get("league") or {}).get("id")) != str(league):
            continue
        for season_entry in item.get("seasons") or []:
            if season_entry.get("year") != season:
                continue
            raw = (season_entry.get("coverage") or {}).get("fixtures") or {}
            if not isinstance(raw, dict):
                raw = {}
            return {
                feature: raw.get(feature) if type(raw.get(feature)) is bool else None
                for feature in FEATURES
            }
    return {feature: None for feature in FEATURES}


def record_league_coverage(
    db: Session, league: int, season: int, response: list[dict],
) -> LeagueCoverageProbe:
    status = parse_league_coverage(response, league, season)
    row = db.scalar(select(LeagueCoverageProbe).where(
        LeagueCoverageProbe.provider == "api-football",
        LeagueCoverageProbe.provider_league_id == str(league),
        LeagueCoverageProbe.season == season,
    ))
    if row is None:
        row = LeagueCoverageProbe(
            provider="api-football", provider_league_id=str(league),
            season=season,
        )
        db.add(row)
    row.reported_events = status["events"]
    row.reported_statistics_fixtures = status["statistics_fixtures"]
    row.reported_statistics_players = status["statistics_players"]
    row.reported_lineups = status["lineups"]
    row.raw_coverage = status
    row.fetched_at = datetime.now(timezone.utc)
    db.flush()
    return row


def coverage_report(db: Session, league: int, season: int) -> dict:
    if league <= 0 or not 2000 <= season <= 2100:
        raise ValueError("Invalid league or season")
    row = db.scalar(select(LeagueCoverageProbe).where(
        LeagueCoverageProbe.provider == "api-football",
        LeagueCoverageProbe.provider_league_id == str(league),
        LeagueCoverageProbe.season == season,
    ))
    status = (
        {name: getattr(row, f"reported_{name}") for name in FEATURES}
        if row else dict.fromkeys(FEATURES)
    )
    return {
        "provider": "api-football",
        "league": league,
        "season": season,
        "probed": row is not None,
        "fetched_at": row.fetched_at.isoformat() if row else None,
        "capabilities": status,
        "all_fixtures_verified": False,
        "all_player_stats_verified": False,
        "usable_for_ml_negative_labels": False,
        "caveat": (
            "Provider league/season flags indicate features that may be "
            "supported, not completed fixture/player-stat coverage. "
            "Unknown flags are null, not false."
        ),
    }


def probe_league(league: int, season: int) -> dict:
    if league <= 0 or not 2000 <= season <= 2100:
        raise ValueError("Invalid league or season")
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY is not configured")
    with SessionLocal.begin() as db:
        reserve_provider_call(db)
    client = ApiFootballClient(
        settings.api_football_key, base_url=settings.api_football_base_url,
    )
    try:
        response = client.fetch_league_coverage(league=league, season=season)
    finally:
        client.close()
    with SessionLocal.begin() as db:
        record_league_coverage(db, league, season, response)
        return coverage_report(db, league, season)
