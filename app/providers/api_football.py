"""API-Football adapter. No credentials are required for tests."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


class ProviderError(RuntimeError):
    """Provider failure without credentials or private response data."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProviderRateLimitError(ProviderError):
    """429 from provider: caller must stop, not automatically retry."""

    def __init__(self, retry_after_seconds: int | None):
        super().__init__("API-Football rate-limited this request", status_code=429)
        self.retry_after_seconds = retry_after_seconds



@dataclass(frozen=True)
class Fixture:
    source_id: str
    kickoff: datetime | None
    status: str
    home_score: int | None
    away_score: int | None
    home_team_id: str
    away_team_id: str
    league_id: str
    home_team_name: str = ""
    away_team_name: str = ""
    league_name: str = ""


def normalize_fixture(item: dict[str, Any]) -> Fixture:
    fixture = item["fixture"]
    teams = item["teams"]
    league = item["league"]
    goals = item.get("goals") or {}
    when = fixture.get("date")
    kickoff = datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None
    return Fixture(
        source_id=str(fixture["id"]),
        kickoff=kickoff,
        status=fixture.get("status", {}).get("short") or "UNKNOWN",
        home_score=goals.get("home"),
        away_score=goals.get("away"),
        home_team_id=str(teams["home"]["id"]),
        away_team_id=str(teams["away"]["id"]),
        league_id=str(league["id"]),
        home_team_name=teams["home"].get("name") or "",
        away_team_name=teams["away"].get("name") or "",
        league_name=league.get("name") or "",
    )


class ApiFootballClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://v3.football.api-sports.io",
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("API-Football key required")
        self.client = httpx.Client(
            base_url=base_url,
            headers={"x-apisports-key": api_key},
            timeout=15,
            transport=transport,
        )

    def _get(self, path: str, params: dict[str, int]) -> list[dict]:
        """One HTTP call only. Any retry must reserve fresh quota externally."""
        try:
            response = self.client.get(path, params=params)
        except httpx.RequestError as exc:
            raise ProviderError("API-Football transport request failed") from exc
        if response.status_code == 429:
            raw = response.headers.get("retry-after", "")
            wait = int(raw) if raw.isdigit() else None
            raise ProviderRateLimitError(wait)
        if response.is_error:
            raise ProviderError(
                "API-Football HTTP request failed",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Invalid API-Football JSON response") from exc
        if not isinstance(data, dict) or data.get("errors"):
            raise ProviderError("API-Football reported an API error")
        items = data.get("response")
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            raise ProviderError("Unexpected API-Football response structure")
        return items

    def fetch_fixtures(self, *, league: int, season: int) -> list[Fixture]:
        try:
            return [
                normalize_fixture(item)
                for item in self._get("/fixtures", {"league": league, "season": season})
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Invalid API-Football fixture data") from exc

    def fetch_league_coverage(self, *, league: int, season: int) -> list[dict]:
        return self._get("/leagues", {"id": league, "season": season})

    def fetch_events(self, *, fixture_id: int) -> list[dict]:
        return self._get("/fixtures/events", {"fixture": fixture_id})

    def fetch_statistics(self, *, fixture_id: int) -> list[dict]:
        return self._get("/fixtures/statistics", {"fixture": fixture_id})

    def fetch_fixture_players(self, *, fixture_id: int) -> list[dict]:
        return self._get("/fixtures/players", {"fixture": fixture_id})

    def fetch_player_profile(self, *, player_id: int, season: int) -> list[dict]:
        return self._get("/players", {"id": player_id, "season": season})

    def close(self) -> None:
        self.client.close()
