"""Disallow CI role-privilege tests on any source or hosted DB."""
import pytest

from tests.restored_readonly_role_smoke import require_disposable_restored_database


@pytest.mark.parametrize(("setting", "bad"), [
    ("GITHUB_ACTIONS", "false"),
    ("FOOTSCORE_INTEGRATION_TESTS", "0"),
    ("PGHOST", "some.production.host"),
    ("PGDATABASE", "real_customer_data"),
    ("PGUSER", "production_admin"),
    ("DATABASE_URL", "postgresql+psycopg://footscore@localhost/footscore_restore_ci"),
    ("DATABASE_URL", "postgresql+psycopg://footscore@127.0.0.1/footscore"),
    ("DATABASE_URL", "postgresql+psycopg://root@127.0.0.1/footscore_restore_ci"),
    ("DATABASE_URL", "sqlite:///footscore_restore_ci"),
])
def test_rejects_source_and_remote_targets(monkeypatch, setting, bad):
    correct = {
        "GITHUB_ACTIONS": "true",
        "FOOTSCORE_INTEGRATION_TESTS": "1",
        "PGHOST": "127.0.0.1",
        "PGDATABASE": "footscore",
        "PGUSER": "footscore",
        "DATABASE_URL": (
            "postgresql+psycopg://footscore:ci-only@"
            "127.0.0.1/footscore_restore_ci"
        ),
    }
    for key, value in correct.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(setting, bad)
    with pytest.raises(RuntimeError, match="disposable"):
        require_disposable_restored_database()


def test_exact_restore_ci_guards_pass(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FOOTSCORE_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGDATABASE", "footscore")
    monkeypatch.setenv("PGUSER", "footscore")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://footscore:ci-only@"
        "127.0.0.1:5432/footscore_restore_ci",
    )
    require_disposable_restored_database()
