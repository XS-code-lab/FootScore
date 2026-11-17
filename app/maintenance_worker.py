"""Optional bounded maintenance worker with explicit mutating mode.

Dry-run by default. For opted-in deployments, a single worker can run:
python -m app.maintenance_worker --execute --watch --interval-seconds 86400

This worker only deletes expired auth state, not user accounts/notifications.
"""
import argparse
import logging
import time

from app.db import SessionLocal
from app.services.maintenance import cleanup_auth_state

LOG = logging.getLogger(__name__)


def run_cleanup(*, execute: bool, max_batches: int, batch_size: int) -> dict:
    """Bound DB work per cycle; avoid an unbounded write loop."""
    if not 1 <= max_batches <= 50 or not 1 <= batch_size <= 10000:
        raise ValueError("Invalid cleanup batch bounds")
    result = {
        "dry_run": not execute,
        "expired_buckets_selected": 0,
        "expired_tokens_selected": 0,
        "batches": 0,
    }
    for _ in range(max_batches):
        with SessionLocal() as db:
            batch = cleanup_auth_state(db, execute=execute, limit=batch_size)
        count = batch["expired_buckets_selected"] + batch["expired_tokens_selected"]
        result["expired_buckets_selected"] += batch["expired_buckets_selected"]
        result["expired_tokens_selected"] += batch["expired_tokens_selected"]
        result["batches"] += 1
        if not execute or count < batch_size:
            break
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded auth-token retention worker")
    parser.add_argument("--execute", action="store_true", help="Delete expired rows")
    parser.add_argument("--watch", action="store_true", help="Repeat on a schedule")
    parser.add_argument("--interval-seconds", type=int, default=86400)
    parser.add_argument("--max-batches", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    if args.watch and not 3600 <= args.interval_seconds <= 604800:
        parser.error("Watch interval must be between 1 hour and 7 days")
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            result = run_cleanup(
                execute=args.execute,
                max_batches=args.max_batches,
                batch_size=args.batch_size,
            )
            LOG.info("Expired authentication maintenance: %s", result)
        except Exception:
            LOG.exception("Expired authentication maintenance failed")
            if not args.watch:
                raise
        if not args.watch:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
