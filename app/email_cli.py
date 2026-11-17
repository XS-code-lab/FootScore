"""Optional SMTP outbox worker, separate from API request handling.

python -m app.email_cli --limit 10
python -m app.email_cli --watch --interval-seconds 30
"""
import argparse
import logging
import time

from app.db import SessionLocal
from app.services.email_outbox import drain_email_outbox

LOG = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver encrypted security emails")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--interval-seconds", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.limit <= 25:
        parser.error("Send limit must be 1..25")
    if args.watch and not 10 <= args.interval_seconds <= 3600:
        parser.error("Watch interval must be 10..3600 seconds")
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            with SessionLocal.begin() as db:
                summary = drain_email_outbox(db, limit=args.limit)
            # Only aggregate counts; never log credentials, codes or emails.
            LOG.info("Email outbox delivery: %s", summary)
        except Exception:
            LOG.exception("Email delivery pass failed")
            if not args.watch:
                raise
        if not args.watch:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
