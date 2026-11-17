"""Import one league manually after applying Alembic migrations."""
import argparse

from app.services.quota import QuotaExceeded
from app.services.sync import sync_league


def main() -> None:
    parser = argparse.ArgumentParser(description="Import fixtures for one league and season")
    parser.add_argument("--league", required=True, type=int)
    parser.add_argument("--season", required=True, type=int)
    args = parser.parse_args()
    try:
        result = sync_league(args.league, args.season)
    except (ValueError, RuntimeError, QuotaExceeded) as exc:
        parser.error(str(exc))
    print(f"Imported/updated {result.imported} fixtures (requests today: "
          f"{result.requests_used})")


if __name__ == "__main__":
    main()
