"""Quota-controlled, manual player statistics/profile requests."""
from dataclasses import dataclass

from app.db import SessionLocal
from app.models import Match
from app.providers.api_football import ApiFootballClient
from app.services.player_stats import upsert_fixture_players, upsert_player_profile
from app.services.quota import reserve_request
from app.settings import settings


@dataclass(frozen=True)
class PlayerSyncResult:
    match_id: int
    imported: int


def _reserve() -> None:
    with SessionLocal.begin() as session:
        reserve_request(
            session,
            limit=settings.provider_daily_limit,
            reserve=settings.provider_reserve,
        )


def sync_fixture_players(match_id: int) -> PlayerSyncResult:
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY not configured")
    with SessionLocal() as session:
        match = session.get(Match, match_id)
        if match is None or match.provider != "api-football":
            raise ValueError("Match not found for API-Football")
        external_id = int(match.provider_id)

    _reserve()
    client = ApiFootballClient(settings.api_football_key,
                               base_url=settings.api_football_base_url)
    try:
        response = client.fetch_fixture_players(fixture_id=external_id)
    finally:
        client.close()

    with SessionLocal.begin() as session:
        persisted = session.get(Match, match_id)
        imported = upsert_fixture_players(session, persisted, response)
    return PlayerSyncResult(match_id=match_id, imported=imported)


def sync_player_profile(provider_player_id: int, season: int) -> int | None:
    if provider_player_id <= 0 or not 2000 <= season <= 2100:
        raise ValueError("Invalid player ID or season")
    if not settings.api_football_key:
        raise RuntimeError("API_FOOTBALL_KEY not configured")
    _reserve()
    client = ApiFootballClient(settings.api_football_key,
                               base_url=settings.api_football_base_url)
    try:
        response = client.fetch_player_profile(
            player_id=provider_player_id, season=season,
        )
    finally:
        client.close()
    with SessionLocal.begin() as session:
        player = upsert_player_profile(session, response, provider_player_id)
        return player.id if player is not None else None
