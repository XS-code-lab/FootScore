"""Explicitly opted-in backup watchdog with optional HTTPS notification.

Examples:
  python -m app.backup_alert_cli --bucket my-private-bucket
  python -m app.backup_alert_cli --bucket my-private-bucket --execute --watch

Dry run still LISTS the remote bucket but never POSTs a webhook or changes DB.
The API and normal Compose startup do not start this worker.
"""
import argparse
import json
import logging
import os
import time

from app.backup_inventory import inspect_remote_backups
from app.backup_remote import validate_destination
from app.db import SessionLocal
from app.services.backup_alerts import deliver_backup_alert, validate_webhook_url

LOG = logging.getLogger(__name__)


def check_once(
    s3, *, bucket: str, prefix: str = "footscore",
    execute: bool = False, session_factory=SessionLocal,
    sender=None, max_age_hours: int = 30, remind_hours: int = 24,
) -> dict:
    """A failed listing is an alarm, never a healthy empty inventory."""
    listing_error = False
    try:
        report = inspect_remote_backups(
            s3, bucket=bucket, prefix=prefix, max_age_hours=max_age_hours,
        )
        condition = report["freshness"]["state"]
    except Exception:
        listing_error = True
        condition = "error"
    if not execute:
        return {
            "dry_run": True, "state": condition, "listing_failed": listing_error,
            "webhook_sent": False, "database_modified": False,
        }
    from app.services.backup_alerts import send_health_webhook

    if sender is None:
        sender = send_health_webhook
    # The SQL transaction is rolled back if the sender fails.
    with session_factory.begin() as db:
        outcome = deliver_backup_alert(
            db, bucket=bucket, prefix=prefix, condition=condition,
            sender=sender, remind_hours=remind_hours,
        )
    return {
        "dry_run": False, "state": condition, "listing_failed": listing_error,
        "webhook_sent": outcome["webhook_sent"],
        "event": outcome["event"], "database_modified": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Optional backup health webhook worker")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="footscore")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-minutes", type=int, default=60)
    parser.add_argument("--max-age-hours", type=int, default=30)
    parser.add_argument("--remind-hours", type=int, default=24)
    args = parser.parse_args()
    if args.watch and not args.execute:
        parser.error("--watch requires explicit --execute")
    if not 5 <= args.interval_minutes <= 1440:
        parser.error("Interval must be 5..1440 minutes")
    if not 1 <= args.max_age_hours <= 744 or not 1 <= args.remind_hours <= 168:
        parser.error("Invalid alert thresholds")
    try:
        validate_destination(args.bucket, args.prefix)
    except ValueError as exc:
        parser.error(str(exc))
    if args.execute:
        try:
            validate_webhook_url(os.environ.get("BACKUP_ALERT_WEBHOOK_URL", ""))
        except ValueError:
            parser.error("Configure a trusted HTTPS BACKUP_ALERT_WEBHOOK_URL")
    logging.basicConfig(level=logging.INFO)
    while True:
        result = None
        try:
            # This worker performs a READ-ONLY cloud listing even without
            # --execute. Only --execute enables DB state and HTTPS alerts.
            import boto3

            result = check_once(
                boto3.client("s3"), bucket=args.bucket, prefix=args.prefix,
                execute=args.execute, max_age_hours=args.max_age_hours,
                remind_hours=args.remind_hours,
            )
            if not args.watch:
                print(json.dumps(result, indent=2))
            else:
                LOG.info(
                    "Backup visibility check: state=%s, webhook_sent=%s",
                    result["state"], result["webhook_sent"],
                )
        except Exception:
            LOG.error("Backup health check or notification failed (details suppressed)")
            if not args.watch:
                raise SystemExit(1) from None
        if not args.watch:
            if result is not None and result["listing_failed"]:
                raise SystemExit(1)
            return
        time.sleep(args.interval_minutes * 60)


if __name__ == "__main__":
    main()
