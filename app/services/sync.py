"""Provider ingestion service. Migrations must run before use."""
from dataclasses import dataclass

from app.db import SessionLocal
from app.providers.api_football import ApiFootballClient
from app.services.ingestion import upsert_fixtures
from app.services.provider_guardrails import reserve_provider_call
from app.settings import settings


@dataclass(frozen=True)
class SyncResult:
    league: int
    season: int
    imported: int
    requests_used: int


def sync_league(league: int, season: int) -> SyncResult:
    if league <= 0 or not (2000 <= season <= 2100):
        raise ValueError("Invalid league or season")
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY is not configured")
    with SessionLocal.begin() as session:
        used = reserve_provider_call(session)
    # Commit reservation before the HTTP call. Retrying requires another reservation.
    client = ApiFootballClient(settings.api_football_key,
                               base_url=settings.api_football_base_url)
    try:
        fixtures = client.fetch_fixtures(league=league, season=season)
    finally:
        client.close()
    with SessionLocal.begin() as session:
        imported = upsert_fixtures(session, fixtures)
    return SyncResult(league=league, season=season, imported=imported, requests_used=used)
