"""Trusted operator CLI for queue lag and local quota usage.

python -m app.ops_cli
python -m app.ops_cli --strict --max-match-age 120 --max-email-age 300

Never expose this summary as an unauthenticated public endpoint.
"""
import argparse
import json

from app.db import SessionLocal
from app.services.operations import assess_snapshot, snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local operations snapshot")
    parser.add_argument("--strict", action="store_true", help="Exit 2 on attention state")
    parser.add_argument("--max-match-age", type=int, default=120)
    parser.add_argument("--max-email-age", type=int, default=300)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            data = snapshot(db)
        data["assessment"] = assess_snapshot(
            data, max_match_age=args.max_match_age,
            max_email_age=args.max_email_age,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(data, indent=2))
    if args.strict and data["assessment"]["state"] == "attention":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
