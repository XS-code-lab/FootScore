"""Guardrails for backup/restore shell scripts without starting PostgreSQL."""
import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _environment() -> dict:
    # Synthetic values only; the guards must run before any network call.
    return {
        **os.environ,
        "PGHOST": "127.0.0.1",
        "PGPORT": "55432",
        "PGUSER": "ci_dummy_user",
        "PGDATABASE": "ci_dummy_source",
        "PGPASSWORD": "dummy-not-a-real-secret",
    }


def test_backup_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    existing = tmp_path / "important.dump"
    existing.write_bytes(b"do not change this archive")
    result = subprocess.run(
        ["sh", str(SCRIPTS / "postgres_backup.sh"), str(existing)],
        capture_output=True, text=True, env=_environment(), check=False,
    )
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
    assert existing.read_bytes() == b"do not change this archive"


def test_backup_refuses_missing_directory(tmp_path: Path) -> None:
    destination = tmp_path / "not-created" / "dump.archive"
    result = subprocess.run(
        ["sh", str(SCRIPTS / "postgres_backup.sh"), str(destination)],
        capture_output=True, text=True, env=_environment(), check=False,
    )
    assert result.returncode != 0
    assert "directory must already exist" in result.stderr
    assert not destination.exists()


def test_restore_rejects_production_and_unsafe_target_names(tmp_path: Path) -> None:
    archive = tmp_path / "test.dump"
    archive.write_bytes(b"mock data")
    for target in (
        "foot_score", "postgres", "ci_dummy_source",
        "footscore_restore_", "footscore_restore_bad-name",
        "footscore_restore_x;DROP DATABASE footscore",
        "footscore_restore_" + "x" * 70,
    ):
        result = subprocess.run(
            ["sh", str(SCRIPTS / "postgres_restore_drill.sh"),
             str(archive), target],
            capture_output=True, text=True, env=_environment(), check=False,
        )
        assert result.returncode != 0, target
        assert "Restore" in result.stderr or "restore" in result.stderr
