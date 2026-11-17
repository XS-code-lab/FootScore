"""API-Football adapter. No credentials are required for tests."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


class ProviderError(RuntimeError):
    pass


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

    def fetch_fixtures(self, *, league: int, season: int) -> list[Fixture]:
        try:
            response = self.client.get("/fixtures", params={"league": league, "season": season})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("API-Football request failed") from exc
        if data.get("errors"):
            raise ProviderError("API-Football reported an API error")
        return [normalize_fixture(item) for item in data.get("response", [])]

    def close(self) -> None:
        self.client.close()
