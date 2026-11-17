"""Read cached league/season capability flags or explicitly probe provider."""
import argparse
import json

from app.db import SessionLocal
from app.services.league_probe import coverage_report, probe_league


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provider league coverage flags (not season completion evidence)",
    )
    parser.add_argument("--league", type=int, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--probe", action="store_true",
        help="Spend one locally reserved provider request to refresh metadata",
    )
    args = parser.parse_args()
    try:
        if args.probe:
            report = probe_league(args.league, args.season)
        else:
            with SessionLocal() as db:
                report = coverage_report(db, args.league, args.season)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
