"""Manual per-fixture detail import, charged to the daily provider quota."""
import argparse

from app.services.quota import QuotaExceeded
from app.services.sync_live import sync_match_details


def main() -> None:
    parser = argparse.ArgumentParser(description="Import match events and team stats")
    parser.add_argument("--match-id", type=int, required=True)
    parser.add_argument("--events-only", action="store_true")
    parser.add_argument("--statistics-only", action="store_true")
    args = parser.parse_args()
    if args.match_id <= 0 or (args.events_only and args.statistics_only):
        parser.error("Invalid match ID or mutually exclusive modes")
    try:
        result = sync_match_details(
            args.match_id,
            events=not args.statistics_only,
            statistics=not args.events_only,
        )
    except (ValueError, RuntimeError, QuotaExceeded) as exc:
        parser.error(str(exc))
    print(f"Match {result.match_id}: {result.events} events; "
          f"{result.statistics} team statistics")


if __name__ == "__main__":
    main()
