"""Run separately: python -m app.scheduler (never in web workers)."""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app.services.quota import QuotaExceeded
from app.services.sync import sync_league
from app.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_pilot_sync() -> None:
    for raw in settings.sync_leagues.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            league_str, season_str = item.split(":", 1)
            result = sync_league(int(league_str), int(season_str))
            logger.info("league=%s season=%s fixtures=%s requests_today=%s",
                        result.league, result.season, result.imported, result.requests_used)
        except QuotaExceeded:
            logger.warning("Daily request budget exhausted; stopping cycle")
            break
        except Exception:
            logger.exception("Failed to sync pilot league %s", item)


def main() -> None:
    if not settings.sync_leagues.strip():
        raise SystemExit("Set SYNC_LEAGUES, e.g. 39:2026")
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_pilot_sync, trigger="interval",
                      minutes=settings.sync_interval_minutes,
                      id="pilot-fixtures", max_instances=1, coalesce=True)
    run_pilot_sync()
    scheduler.start()


if __name__ == "__main__":
    main()
