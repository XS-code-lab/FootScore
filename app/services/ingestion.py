from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Competition, Match, Team
from app.providers.api_football import Fixture
from app.services.notifications import FINAL_STATUSES, notify_final_ratings


def upsert_fixtures(db: Session, fixtures: list[Fixture]) -> int:
    """Idempotent upsert. Caller controls transaction and provider request budget."""
    count = 0
    for incoming in fixtures:
        league = db.scalar(select(Competition).where(
            Competition.provider == "api-football",
            Competition.provider_id == incoming.league_id,
        ))
        if league is None:
            league = Competition(
                name=incoming.league_name or f"League {incoming.league_id}",
                provider="api-football", provider_id=incoming.league_id,
            )
            db.add(league)
            db.flush()
        else:
            league.name = incoming.league_name or league.name
        team_ids = []
        for pid, name in (
            (incoming.home_team_id, incoming.home_team_name),
            (incoming.away_team_id, incoming.away_team_name),
        ):
            team = db.scalar(select(Team).where(
                Team.provider == "api-football", Team.provider_id == pid,
            ))
            if team is None:
                team = Team(
                    name=name or f"Team {pid}",
                    provider="api-football", provider_id=pid,
                )
                db.add(team)
                db.flush()
            else:
                team.name = name or team.name
            team_ids.append(team.id)
        match = db.scalar(select(Match).where(
            Match.provider == "api-football", Match.provider_id == incoming.source_id,
        ))
        previous_status = match.status if match is not None else None
        if match is None:
            match = Match(provider="api-football", provider_id=incoming.source_id)
            db.add(match)
        match.competition_id = league.id
        match.home_team_id, match.away_team_id = team_ids
        match.kickoff = incoming.kickoff
        match.status = incoming.status
        match.home_score = incoming.home_score
        match.away_score = incoming.away_score
        match.observed_at = datetime.now(timezone.utc)
        match.updated_at = datetime.now(timezone.utc)
        db.flush()
        if previous_status not in FINAL_STATUSES and match.status in FINAL_STATUSES:
            notify_final_ratings(db, match)
        count += 1
    db.flush()
    return count
