from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_upgrade_and_downgrade(tmp_path: Path, monkeypatch) -> None:
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    from app.settings import settings

    monkeypatch.setattr(settings, "database_url", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {"matches", "players", "provider_quota", "team_match_stats", "alembic_version",
            "users", "favorite_players", "notification_preferences", "notifications",
            "push_devices", "push_deliveries", "live_updates",
            "historical_player_fetches", "provider_rate_locks",
            "provider_request_log", "league_coverage_probes",
            "auth_rate_buckets", "auth_one_time_tokens",
            "email_deliveries"} <= tables
    assert "ix_live_updates_published_created" in {
        ix["name"] for ix in inspect(engine).get_indexes("live_updates")
    }
    command.downgrade(cfg, "base")
    assert "matches" not in inspect(engine).get_table_names()
    assert "team_match_stats" not in inspect(engine).get_table_names()
    assert "notifications" not in inspect(engine).get_table_names()
    assert "live_updates" not in inspect(engine).get_table_names()
    assert "historical_player_fetches" not in inspect(engine).get_table_names()
    assert "provider_request_log" not in inspect(engine).get_table_names()
    assert "league_coverage_probes" not in inspect(engine).get_table_names()
    assert "auth_rate_buckets" not in inspect(engine).get_table_names()
    assert "auth_one_time_tokens" not in inspect(engine).get_table_names()
    assert "email_deliveries" not in inspect(engine).get_table_names()
