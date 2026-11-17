"""Bounded, explicit historical fixture/player data backfill.

Examples:
python -m app.historical_cli --competition-id 1 --start 2024-07-01 --end 2025-06-30
python -m app.historical_cli --competition-id 1 --start 2024-07-01 --end 2025-06-30 --report
python -m app.historical_cli --competition-id 1 --start 2024-07-01 --end 2025-06-30 --execute
"""
import argparse
import json
import time
from datetime import date

from app.db import SessionLocal
from app.services.historical import (
    audit_fixture_player_coverage, plan_historical_backfill,
)
from app.services.sync_players import sync_fixture_players
from app.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely plan historical player imports")
    parser.add_argument("--competition-id", type=int, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--max-requests", type=int, default=5)
    parser.add_argument("--retry-empty", action="store_true")
    parser.add_argument("--report", action="store_true", help="Local evidence report")
    parser.add_argument("--execute", action="store_true", help="Consume real provider quota")
    args = parser.parse_args()

    if args.report and args.execute:
        parser.error("--report and --execute cannot be combined")
    try:
        with SessionLocal() as db:
            if args.report:
                report = audit_fixture_player_coverage(
                    db, competition_id=args.competition_id,
                    start=args.start, end=args.end,
                )
                print(json.dumps(report, indent=2))
                return
            plan = plan_historical_backfill(
                db, competition_id=args.competition_id,
                start=args.start, end=args.end,
                max_requests=args.max_requests, retry_empty=args.retry_empty,
            )
        print(json.dumps(plan, indent=2))
        if not args.execute:
            return
        if not settings.api_football_key:
            parser.error("Set API_FOOTBALL_KEY before --execute")
        print("Executing explicit, rate-limited backfill; empty data remains unverified.")
        for index, row in enumerate(plan["selected"]):
            if index:
                # Free plan 10 requests/min. Shared API callers need a
                # global rate limiter before concurrent use.
                time.sleep(7)
            result = sync_fixture_players(row["match_id"])
            print(json.dumps({
                "match_id": result.match_id,
                "players_imported": result.imported,
                "coverage_verified": False,
            }))
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
