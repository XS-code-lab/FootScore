"""Quota-controlled player fixture/profile import; run after alembic upgrade head."""
import argparse

from app.services.sync_players import sync_fixture_players, sync_player_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="FootScore player data import")
    subcommands = parser.add_subparsers(dest="command", required=True)
    fixture = subcommands.add_parser("fixture", help="Import match player ratings")
    fixture.add_argument("--match-id", type=int, required=True)
    player = subcommands.add_parser("profile", help="Enrich verified player date of birth")
    player.add_argument("--provider-player-id", type=int, required=True)
    player.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    if args.command == "fixture":
        if args.match_id <= 0:
            parser.error("Invalid match ID")
        result = sync_fixture_players(args.match_id)
        print(f"Match {result.match_id}: {result.imported} player statistics imported")
    else:
        result = sync_player_profile(args.provider_player_id, args.season)
        print(f"Profile import result: {result if result is not None else 'not found'}")


if __name__ == "__main__":
    main()
