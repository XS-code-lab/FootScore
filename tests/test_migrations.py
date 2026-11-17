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
            "historical_player_fetches"} <= tables
    command.downgrade(cfg, "base")
    assert "matches" not in inspect(engine).get_table_names()
    assert "team_match_stats" not in inspect(engine).get_table_names()
    assert "notifications" not in inspect(engine).get_table_names()
    assert "live_updates" not in inspect(engine).get_table_names()
    assert "historical_player_fetches" not in inspect(engine).get_table_names()
