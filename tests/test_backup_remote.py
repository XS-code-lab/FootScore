"""Opt-in backup upload tests; absolutely no AWS/S3 network calls."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from app.backup_crypto import encrypt_archive
from app.backup_remote import (
    fetch_and_verify_encrypted_archive, store_encrypted_archive,
    validate_destination,
)
from app.backup_scheduler import backup_once


class FakeS3:
    """Small in-memory substitute enforcing conditional object creation."""

    def __init__(self, *, fail_put=False, corrupt_head=False):
        self.objects = {}
        self.calls = []
        self.fail_put = fail_put
        self.corrupt_head = corrupt_head

    def put_object(self, **kw):
        self.calls.append("put")
        if self.fail_put:
            raise OSError("synthetic upload refusal")
        assert kw["IfNoneMatch"] == "*"
        assert kw["ContentType"] == "application/octet-stream"
        assert kw["ContentLength"] > 36
        assert kw["ChecksumSHA256"]
        key = (kw["Bucket"], kw["Key"])
        if key in self.objects:
            raise ValueError("Conditional create rejected overwrite")
        data = kw["Body"].read()
        assert len(data) == kw["ContentLength"]
        assert data.startswith(b"FSBKUP01")
        self.objects[key] = (data, kw["Metadata"])
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def head_object(self, *, Bucket, Key):
        self.calls.append("head")
        data, metadata = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(data) + (1 if self.corrupt_head else 0),
            "Metadata": metadata,
        }

    def get_object(self, *, Bucket, Key):
        from io import BytesIO

        self.calls.append("get")
        data, _ = self.objects[(Bucket, Key)]
        return {"Body": BytesIO(data)}


@pytest.fixture
def cryptokey(monkeypatch):
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "a1" * 32)


def test_dry_run_does_not_touch_database_cloud_or_secrets(monkeypatch):
    monkeypatch.delenv("FOOTSCORE_BACKUP_KEY", raising=False)
    for name in ("PGHOST", "PGUSER", "PGDATABASE"):
        monkeypatch.delenv(name, raising=False)
    s3 = FakeS3()
    plan = backup_once(bucket="footscore-test-bucket", s3=s3)
    assert plan == {
        "dry_run": True, "remote_upload": False, "encryption": "AES-256-GCM",
        "note": "No database or cloud connection was attempted",
    }
    assert not s3.calls


def test_s3_client_receives_only_authenticated_ciphertext(tmp_path, cryptokey):
    source = tmp_path / "plain.dump"
    source.write_bytes(b"synthetic-private-database" * 500)
    encrypted = tmp_path / "backup.fsbak"
    encrypt_archive(source, encrypted)
    s3 = FakeS3()
    key = "footscore/2026/11/18/random-test.fsbak"
    result = store_encrypted_archive(
        s3, path=encrypted, bucket="footscore-test-bucket", key=key,
    )
    assert result["uploaded"]
    assert result["bytes"] == encrypted.stat().st_size
    assert result["object_key"] == key
    assert s3.calls == ["put", "head"]
    remote_bytes, metadata = s3.objects[("footscore-test-bucket", key)]
    assert b"synthetic-private-database" not in remote_bytes
    assert metadata["format"] == "fsbkup01"
    assert metadata["footscore-sha256"] == result["sha256"]
    downloaded = tmp_path / "downloaded.fsbak"
    fetch_and_verify_encrypted_archive(
        s3, bucket="footscore-test-bucket", key=key, destination=downloaded,
    )
    assert downloaded.read_bytes() == remote_bytes
    assert s3.calls == ["put", "head", "get"]
    with pytest.raises(ValueError, match="overwrite"):
        fetch_and_verify_encrypted_archive(
            s3, bucket="footscore-test-bucket", key=key, destination=downloaded,
        )
    # No remote deletes are ever performed.
    assert len(s3.objects) == 1


def test_s3_upload_refuses_plaintext_tampering_and_invalid_bucket(tmp_path, cryptokey):
    plain = tmp_path / "plain.dump"
    plain.write_bytes(b"never upload plaintext")
    s3 = FakeS3()
    with pytest.raises(ValueError, match="unencrypted"):
        store_encrypted_archive(
            s3, path=plain, bucket="footscore-test-bucket", key="footscore/x.fsbak",
        )
    assert not s3.calls
    for bucket, prefix in (
        ("Invalid_BUCKET", "footscore"), ("a", "footscore"),
        ("safe-bucket", "../other"), ("safe-bucket", "/absolute"),
        ("safe-bucket", "path//bad"), ("safe-bucket", "path/"),
    ):
        with pytest.raises(ValueError):
            validate_destination(bucket, prefix)
    encrypted = tmp_path / "archive.fsbak"
    encrypt_archive(plain, encrypted)
    tampered = tmp_path / "tampered.fsbak"
    corrupted = bytearray(encrypted.read_bytes())
    corrupted[-4] ^= 1
    tampered.write_bytes(corrupted)
    with pytest.raises(InvalidTag):
        store_encrypted_archive(
            s3, path=tampered,
            bucket="footscore-test-bucket", key="footscore/y.fsbak",
        )
    assert not s3.calls


def test_head_mismatch_blocks_success(tmp_path, cryptokey):
    plain = tmp_path / "plain.dump"
    plain.write_bytes(b"safe synthetic archive")
    encrypted = tmp_path / "archive.fsbak"
    encrypt_archive(plain, encrypted)
    with pytest.raises(RuntimeError, match="metadata"):
        store_encrypted_archive(
            FakeS3(corrupt_head=True), path=encrypted,
            bucket="footscore-test-bucket", key="footscore/test.fsbak",
        )


def test_backup_run_sends_only_encrypted_archive_and_drops_temp_plain(
    tmp_path, monkeypatch, cryptokey,
):
    import app.backup_scheduler as module

    marker = tmp_path / "fake-backup.sh"
    marker.write_text("test-only")
    monkeypatch.setattr(module, "SCRIPT", marker)
    for var, value in [
        ("PGHOST", "localhost"), ("PGUSER", "test-only"),
        ("PGDATABASE", "synthetic-test"),
    ]:
        monkeypatch.setenv(var, value)
    captured_paths = []

    def fake_dump(args, **kwargs):
        assert kwargs["check"]
        assert kwargs["timeout"] == 900
        assert args[0] == "sh"
        path = Path(args[-1])
        captured_paths.append(path)
        path.write_bytes(b"synthetic-database" * 400)

    monkeypatch.setattr(module.subprocess, "run", fake_dump)
    s3 = FakeS3()
    result = backup_once(
        bucket="footscore-test-bucket", prefix="footscore",
        execute=True, workspace=tmp_path, s3=s3,
        now=datetime(2026, 11, 18, 0, 1, tzinfo=timezone.utc),
    )
    assert result["remote_upload"] is True
    assert result["remote_download_restore_verified"] is False
    assert result["object_key"].startswith("footscore/2026/11/18/")
    assert result["object_key"].endswith(".fsbak")
    assert result["duration_seconds"] >= 0
    assert s3.calls == ["put", "head"]
    assert all(not path.exists() for path in captured_paths)
    assert len(s3.objects) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fake-backup.sh"]


def test_failed_upload_cleans_local_data_without_remote_claim(
    tmp_path, monkeypatch, cryptokey,
):
    import app.backup_scheduler as module

    marker = tmp_path / "fake-backup.sh"
    marker.write_text("test-only")
    monkeypatch.setattr(module, "SCRIPT", marker)
    for name in ("PGHOST", "PGUSER", "PGDATABASE"):
        monkeypatch.setenv(name, "synthetic-only")

    def dump(args, **kwargs):
        Path(args[-1]).write_bytes(b"sensitive-synthetic-data")

    monkeypatch.setattr(module.subprocess, "run", dump)
    s3 = FakeS3(fail_put=True)
    with pytest.raises(OSError, match="synthetic upload refusal"):
        backup_once(
            bucket="footscore-test-bucket", execute=True, workspace=tmp_path, s3=s3,
        )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fake-backup.sh"]


def test_missing_key_or_database_conf_refuses_execution(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("FOOTSCORE_BACKUP_KEY", raising=False)
    with pytest.raises(ValueError, match="FOOTSCORE_BACKUP_KEY"):
        backup_once(
            bucket="footscore-test-bucket", execute=True,
            workspace=tmp_path, s3=FakeS3(),
        )
