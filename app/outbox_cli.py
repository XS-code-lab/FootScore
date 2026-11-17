"""Bounded retrying Redis outbox publisher; durable replay remains in SQL.

python -m app.outbox_cli
python -m app.outbox_cli --watch
"""
import argparse
import logging
import time

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.services.live_updates import drain_outbox
from app.settings import settings

LOG = logging.getLogger(__name__)


def retry_delay(failures: int, *, cap_seconds: int = 30) -> int:
    """Exponential retry with an upper bound; no zero-delay spin loop."""
    if failures < 1 or not 1 <= cap_seconds <= 300:
        raise ValueError("Invalid retry count or backoff ceiling")
    return min(cap_seconds, 2 ** min(failures - 1, 9))


def publish_once(redis_client, *, session_factory=SessionLocal, limit: int = 100) -> int:
    """Commit ack only after successful Redis calls.

    If any publish fails, the transaction rolls back all acks for the
    batch. Some subscribers may have received earlier messages, so REST
    consumers must deduplicate by update id after Redis reconnect.
    """
    with session_factory.begin() as db:
        return drain_outbox(db, redis_client, limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish committed football updates")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-backoff-seconds", type=int, default=30)
    args = parser.parse_args()
    if not settings.redis_url:
        parser.error("REDIS_URL is required")
    if not 1 <= args.limit <= 1000:
        parser.error("Outbox batch size must be 1..1000")
    if not 1 <= args.max_backoff_seconds <= 300:
        parser.error("Maximum backoff must be 1..300 seconds")
    logging.basicConfig(level=logging.INFO)
    # A stalled Redis connection must not hold PostgreSQL row locks forever.
    client = Redis.from_url(
        settings.redis_url, socket_connect_timeout=3,
        socket_timeout=3, health_check_interval=30,
    )
    failures = 0
    try:
        while True:
            try:
                count = publish_once(client, limit=args.limit)
                failures = 0
                if count:
                    LOG.info("Published %d match updates", count)
                delay = 1
            except (RedisError, SQLAlchemyError, OSError):
                # Some connection exceptions contain credentials/URLs;
                # never log their message or HTTP-like request content.
                failures += 1
                delay = retry_delay(
                    failures, cap_seconds=args.max_backoff_seconds,
                )
                LOG.warning(
                    "Match update publisher unavailable; retrying in %d seconds",
                    delay,
                )
                if not args.watch:
                    raise
            if not args.watch:
                break
            time.sleep(delay)
    finally:
        client.close()


if __name__ == "__main__":
    main()
