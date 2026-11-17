"""Development ingestion command: python -m app.cli --league 39 --season 2026."""
import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import ProviderQuota
from app.providers.api_football import ApiFootballClient
from app.services.ingestion import upsert_fixtures
from app.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a league's fixtures into the database")
    parser.add_argument("--league", required=True, type=int)
    parser.add_argument("--season", required=True, type=int)
    args = parser.parse_args()
    if not settings.api_football_key:
        parser.error("API_FOOTBALL_KEY must be configured in .env")
    if args.league <= 0 or args.season < 2000:
        parser.error("Invalid league or season")

    # Development bootstrap only. Production will use Alembic migrations.
    Base.metadata.create_all(engine)
    day = datetime.now(timezone.utc).date()
    with SessionLocal.begin() as db:
        quota = db.scalar(select(ProviderQuota).where(
            ProviderQuota.provider == "api-football", ProviderQuota.day == day,
        ))
        if quota is None:
            quota = ProviderQuota(provider="api-football", day=day, used=0)
            db.add(quota)
            db.flush()
        if quota.used >= settings.provider_daily_limit - settings.provider_reserve:
            parser.error("Daily provider request budget exhausted")
        quota.used += 1

    client = ApiFootballClient(
        settings.api_football_key, base_url=settings.api_football_base_url,
    )
    try:
        fixtures = client.fetch_fixtures(league=args.league, season=args.season)
    finally:
        client.close()
    with SessionLocal.begin() as db:
        count = upsert_fixtures(db, fixtures)
    print(f"Imported/updated {count} fixtures")


if __name__ == "__main__":
    main()
