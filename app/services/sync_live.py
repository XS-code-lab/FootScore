"""Explicit per-match detail synchronization; every endpoint costs a quota unit."""
from dataclasses import dataclass

from app.db import SessionLocal
from app.models import Match
from app.providers.api_football import ApiFootballClient
from app.services.live import upsert_match_events, upsert_match_statistics
from app.services.quota import reserve_request
from app.settings import settings


@dataclass(frozen=True)
class LiveSyncResult:
    match_id: int
    events: int
    statistics: int


def sync_match_details(match_id: int, *, events: bool = True,
                       statistics: bool = True) -> LiveSyncResult:
    if not events and not statistics:
        raise ValueError("Enable at least one detail type")
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY is not configured")
    with SessionLocal() as session:
        match = session.get(Match, match_id)
        if match is None:
            raise ValueError("Unknown FootScore match ID")
        fixture_id = int(match.provider_id)
    client = ApiFootballClient(
        settings.api_football_key, base_url=settings.api_football_base_url,
    )
    events_count = 0
    stats_count = 0
    try:
        if events:
            with SessionLocal.begin() as session:
                reserve_request(session, limit=settings.provider_daily_limit,
                                reserve=settings.provider_reserve)
            raw = client.fetch_events(fixture_id=fixture_id)
            with SessionLocal.begin() as session:
                events_count = upsert_match_events(
                    session, session.get(Match, match_id), raw, reconcile=True,
                )
        if statistics:
            with SessionLocal.begin() as session:
                reserve_request(session, limit=settings.provider_daily_limit,
                                reserve=settings.provider_reserve)
            raw = client.fetch_statistics(fixture_id=fixture_id)
            with SessionLocal.begin() as session:
                stats_count = upsert_match_statistics(
                    session, session.get(Match, match_id), raw,
                )
    finally:
        client.close()
    return LiveSyncResult(match_id=match_id, events=events_count,
                          statistics=stats_count)
