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
    assert {"matches", "players", "provider_quota", "alembic_version"} <= tables
    command.downgrade(cfg, "base")
    assert "matches" not in inspect(engine).get_table_names()
