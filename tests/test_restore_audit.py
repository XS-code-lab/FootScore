"""Independent recovery checks use fake S3 and mocked restore execution.

Real PostgreSQL restore is verified in the separate disposable CI job.
"""
import io
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.backup_crypto import encrypt_archive
from app.restore_audit import (
    audit_restored_database, restore_once, validate_archive_key,
    validate_drill_target,
)

BUCKET = "footscore-ci-restore"
TARGET = "footscore_restore_safecheck"
KEY = "footscore/2026/11/18/20261118T120000000000Z-" + "a" * 32 + ".fsbak"


class MemoryS3:
    def __init__(self, data: bytes):
        self.data = data
        self.calls = 0
    def get_object(self, *, Bucket: str, Key: str):
        assert (Bucket, Key) == (BUCKET, KEY)
        self.calls += 1
        return {"Body": io.BytesIO(self.data)}


def key_env(monkeypatch):
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "f0" * 32)
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGUSER", "ci-only")
    monkeypatch.setenv("PGDATABASE", "synthetic_source")


def sample_encrypted(tmp_path: Path, monkeypatch) -> bytes:
    key_env(monkeypatch)
    source = tmp_path / "source.dump"
    source.write_bytes(b"FootScore isolated synthetic archive\x00" * 400)
    encrypted = tmp_path / "encrypted.fsbak"
    encrypt_archive(source, encrypted)
    return encrypted.read_bytes()


def test_plan_needs_no_cloud_db_or_encryption_secret(monkeypatch):
    for var in ("PGHOST", "PGUSER", "PGDATABASE", "FOOTSCORE_BACKUP_KEY"):
        monkeypatch.delenv(var, raising=False)
    report = restore_once(bucket=BUCKET, key=KEY, target=TARGET)
    assert report["dry_run"] is True
    assert report["remote_download"] is False
    assert report["database_created"] is False


@pytest.mark.parametrize("target", [
    "footscore", "postgres", "footscore_restore_",
    "footscore_restore_bad-name", "footscore_restore_../production",
    "footscore_restore_" + "a" * 50, "footscore_restore_existing",
])
def test_unsafe_targets_are_rejected_before_any_cloud_call(target):
    source = "footscore_restore_existing" if target.endswith("_existing") else "footscore"
    with pytest.raises(ValueError, match="NEW"):
        validate_drill_target(target, source)


@pytest.mark.parametrize("key", [
    "../private/2026/11/18/x.fsbak",
    "footscore/ci/synthetic.fsbak",
    "footscore/2026/11/17/20261118T120000000000Z-" + "a" * 32 + ".fsbak",
    "other/2026/11/18/20261118T120000000000Z-" + "a" * 32 + ".fsbak",
    "footscore/2026/99/18/20269918T120000000000Z-" + "a" * 32 + ".fsbak",
])
def test_only_generated_archives_accepted(key):
    with pytest.raises(ValueError):
        validate_archive_key("footscore", key)


def test_isolated_restore_verifies_ciphertext_and_audits_new_target(
    tmp_path, monkeypatch,
):
    ciphertext = sample_encrypted(tmp_path, monkeypatch)
    store = MemoryS3(ciphertext)
    observed = []

    def fake_runner(argv, **kwargs):
        assert argv[:2] == ["sh", str(Path(argv[1]))]
        assert argv[-1] == TARGET
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 900
        plain_path = Path(argv[2])
        assert plain_path.read_bytes() == b"FootScore isolated synthetic archive\x00" * 400
        observed.append(plain_path)

    def fake_audit(target):
        assert target == TARGET
        assert observed and observed[0].exists()
        return {
            "target": target, "schema_matches_current_head": True,
            "row_counts": {"matches": 1, "users": 1},
            "source_completeness_verified": False,
        }

    result = restore_once(
        bucket=BUCKET, key=KEY, target=TARGET, execute=True,
        s3=store, workspace=tmp_path, runner=fake_runner, auditor=fake_audit,
    )
    assert store.calls == 1
    assert result["cryptographic_integrity_verified"]
    assert result["restored_schema_checked"]
    assert not result["rpo_rto_slo_verified"]
    assert result["audit"]["row_counts"] == {"matches": 1, "users": 1}
    assert not observed[0].exists()
    assert set(x.name for x in tmp_path.iterdir()) == {
        "source.dump", "encrypted.fsbak",
    }


def test_tampered_remote_is_never_passed_to_postgres(tmp_path, monkeypatch):
    altered = bytearray(sample_encrypted(tmp_path, monkeypatch))
    altered[-2] ^= 1
    store = MemoryS3(bytes(altered))
    invoked = []

    with pytest.raises(Exception):
        restore_once(
            bucket=BUCKET, key=KEY, target=TARGET, execute=True,
            s3=store, workspace=tmp_path,
            runner=lambda *a, **kw: invoked.append(a),
            auditor=lambda target: {"unreachable": True},
        )
    assert not invoked
    assert store.calls == 1
    assert set(x.name for x in tmp_path.iterdir()) == {
        "source.dump", "encrypted.fsbak",
    }


def test_incorrect_backup_key_rejects_recovery(tmp_path, monkeypatch):
    ciphertext = sample_encrypted(tmp_path, monkeypatch)
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "ab" * 32)
    store = MemoryS3(ciphertext)
    with pytest.raises(Exception):
        restore_once(
            bucket=BUCKET, key=KEY, target=TARGET, execute=True,
            s3=store, workspace=tmp_path,
            runner=lambda *a, **kw: pytest.fail("restore was started"),
        )


def test_missing_pg_secrets_and_prefixed_source_are_refused(tmp_path, monkeypatch):
    sample_encrypted(tmp_path, monkeypatch)
    monkeypatch.delenv("PGHOST")
    with pytest.raises(ValueError, match="PGHOST"):
        restore_once(
            bucket=BUCKET, key=KEY, target=TARGET, execute=True,
            s3=MemoryS3(b"not-authenticated"),
        )
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGDATABASE", "footscore_restore_source")
    with pytest.raises(ValueError, match="Source PGDATABASE"):
        restore_once(
            bucket=BUCKET, key=KEY, target=TARGET, execute=True,
            s3=MemoryS3(b"not-authenticated"),
        )


def test_verify_existing_target_rejects_wrong_source_before_network(monkeypatch):
    monkeypatch.setenv("PGDATABASE", TARGET)
    with pytest.raises(ValueError, match="NEW"):
        audit_restored_database(TARGET)
