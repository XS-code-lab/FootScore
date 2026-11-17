"""Safety guards for the restored FastAPI smoke harness."""
import pytest

from tests.restored_api_smoke import _require_isolated_ci


def test_smoke_rejects_missing_ci_guards(monkeypatch):
    for key in ("GITHUB_ACTIONS", "FOOTSCORE_INTEGRATION_TESTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ci:secret@127.0.0.1:5432/footscore_restore_ci",
    )
    monkeypatch.setenv("PGDATABASE", "footscore")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    with pytest.raises(RuntimeError, match="disposable CI"):
        _require_isolated_ci()


def test_smoke_refuses_source_database_and_remote_database(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FOOTSCORE_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("PGDATABASE", "footscore")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    for url in (
        "postgresql+psycopg://ci:secret@127.0.0.1:5432/footscore",
        "postgresql+psycopg://ci:secret@remote.test:5432/footscore_restore_ci",
    ):
        monkeypatch.setenv("DATABASE_URL", url)
        with pytest.raises(RuntimeError, match="isolated"):
            _require_isolated_ci()


def test_smoke_accepts_only_disposable_local_ci_target(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FOOTSCORE_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("PGDATABASE", "footscore")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ci:secret@127.0.0.1:5432/footscore_restore_ci",
    )
    _require_isolated_ci()
