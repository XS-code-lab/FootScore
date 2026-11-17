"""Run a single publisher: python -m app.outbox_cli --watch."""
import argparse
import logging
import time

from redis import Redis

from app.db import SessionLocal
from app.services.live_updates import drain_outbox
from app.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if not settings.redis_url:
        parser.error("REDIS_URL is required")
    logging.basicConfig(level=logging.INFO)
    redis = Redis.from_url(settings.redis_url)
    try:
        while True:
            try:
                with SessionLocal.begin() as session:
                    count = drain_outbox(session, redis)
                if count:
                    logging.info("Published %d match updates", count)
            except Exception:
                logging.exception("Match outbox publish failed")
                if not args.watch:
                    raise
            if not args.watch:
                break
            time.sleep(1.0)
    finally:
        redis.close()


if __name__ == "__main__":
    main()
