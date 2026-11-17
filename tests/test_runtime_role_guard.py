"""Runtime role smoke must never run against a source or hosted DB."""
import pytest

from tests.restored_runtime_role_smoke import require_disposable_ci


def valid(monkeypatch):
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "FOOTSCORE_INTEGRATION_TESTS": "1",
        "PGHOST": "127.0.0.1",
        "PGDATABASE": "footscore",
        "PGUSER": "footscore",
        "DATABASE_URL": (
            "postgresql+psycopg://footscore:ci-only-not-production@"
            "127.0.0.1:5432/footscore_restore_ci"
        ),
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(("key", "bad"), [
    ("GITHUB_ACTIONS", "false"),
    ("FOOTSCORE_INTEGRATION_TESTS", "0"),
    ("PGHOST", "db.example.net"),
    ("PGDATABASE", "footscore_restore_ci"),
    ("PGUSER", "runtime"),
    ("DATABASE_URL", "sqlite:///local"),
    ("DATABASE_URL", "postgresql+psycopg://footscore@127.0.0.1/footscore"),
    ("DATABASE_URL", "postgresql+psycopg://footscore@remote/footscore_restore_ci"),
    ("DATABASE_URL", "postgresql+psycopg://other@127.0.0.1/footscore_restore_ci"),
])
def test_refuse_unsafe_runtime_role_smoke(monkeypatch, key, bad):
    valid(monkeypatch)
    monkeypatch.setenv(key, bad)
    with pytest.raises(RuntimeError, match="disposable"):
        require_disposable_ci()


def test_exact_disposable_runtime_smoke_is_allowed(monkeypatch):
    valid(monkeypatch)
    require_disposable_ci()
