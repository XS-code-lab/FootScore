"""Safe dry-run of expired auth-state cleanup.

python -m app.maintenance_cli
python -m app.maintenance_cli --execute --limit 1000
"""
import argparse
import json

from app.db import SessionLocal
from app.services.maintenance import cleanup_auth_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove expired auth state")
    parser.add_argument("--execute", action="store_true", help="Really delete rows")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    with SessionLocal() as db:
        result = cleanup_auth_state(db, execute=args.execute, limit=args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
