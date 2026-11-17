"""Backup archive encryption / decryption and tamper-resistant restore tests."""
import os
import stat
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from app.backup_crypto import decrypt_archive, encrypt_archive


@pytest.fixture
def backup_key(monkeypatch):
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "bc" * 32)
    return "bc" * 32


def test_encrypted_backup_roundtrip_streams_large_archives(
    tmp_path: Path, backup_key: str,
) -> None:
    source = tmp_path / "source.dump"
    original = os.urandom(2_100_123)
    source.write_bytes(original)
    encrypted = tmp_path / "source.fsbak"
    restored = tmp_path / "restored.dump"
    encrypt_archive(source, encrypted)
    assert encrypted.read_bytes().startswith(b"FSBKUP01")
    assert original[:100] not in encrypted.read_bytes()
    assert encrypted.stat().st_size == len(original) + 8 + 12 + 16
    assert stat.S_IMODE(encrypted.stat().st_mode) == 0o600
    decrypt_archive(encrypted, verify_only=True)
    assert not restored.exists()
    decrypt_archive(encrypted, restored)
    assert restored.read_bytes() == original
    assert stat.S_IMODE(restored.stat().st_mode) == 0o600


def test_modifying_ciphertext_or_tag_never_publishes_plaintext(
    tmp_path: Path, backup_key: str,
) -> None:
    source = tmp_path / "source.dump"
    source.write_bytes(b"Sensitive synthetic test records" * 100)
    encrypted = tmp_path / "source.fsbak"
    encrypt_archive(source, encrypted)
    data = encrypted.read_bytes()
    for pos in (0, 22, len(data) - 1):
        altered = bytearray(data)
        altered[pos] ^= 0x01
        corrupted = tmp_path / f"corrupt-{pos}.fsbak"
        corrupted.write_bytes(altered)
        output = tmp_path / f"decrypted-{pos}.dump"
        with pytest.raises((InvalidTag, ValueError)):
            decrypt_archive(corrupted, output)
        assert not output.exists()
        assert not list(tmp_path.glob(".footscore-backup-*"))


def test_wrong_key_fails_closed_and_keeps_original(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.dump"
    source.write_bytes(b"test data" * 100)
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "02" * 32)
    encrypted = tmp_path / "encrypted.fsbak"
    encrypt_archive(source, encrypted)
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "aa" * 32)
    with pytest.raises(InvalidTag):
        decrypt_archive(encrypted, verify_only=True)
    restored = tmp_path / "restore.dump"
    with pytest.raises(InvalidTag):
        decrypt_archive(encrypted, restored)
    assert not restored.exists()
    assert source.read_bytes() == b"test data" * 100


def test_backup_crypto_refuses_to_replace_preexisting_files(
    tmp_path: Path, backup_key: str,
) -> None:
    source = tmp_path / "original.dump"
    source.write_bytes(b"original")
    output = tmp_path / "encrypted.fsbak"
    output.write_bytes(b"critical-existing-file")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        encrypt_archive(source, output)
    assert output.read_bytes() == b"critical-existing-file"
    output.unlink()
    encrypt_archive(source, output)
    destination = tmp_path / "existing-restore.dump"
    destination.write_bytes(b"do not overwrite")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        decrypt_archive(output, destination)
    assert destination.read_bytes() == b"do not overwrite"
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        encrypt_archive(source, source)


def test_backup_crypto_rejects_symlink_input_and_missing_secret(
    tmp_path: Path, monkeypatch,
) -> None:
    raw = tmp_path / "archive.dump"
    raw.write_bytes(b"data")
    link = tmp_path / "linked.dump"
    link.symlink_to(raw)
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "99" * 32)
    with pytest.raises(ValueError, match="non-symlink"):
        encrypt_archive(link, tmp_path / "nope.fsbak")
    monkeypatch.delenv("FOOTSCORE_BACKUP_KEY")
    with pytest.raises(ValueError, match="Configure a separate"):
        encrypt_archive(raw, tmp_path / "nope.fsbak")
    monkeypatch.setenv("FOOTSCORE_BACKUP_KEY", "not-hex" * 9 + "0")
    with pytest.raises(ValueError):
        encrypt_archive(raw, tmp_path / "nope.fsbak")
    assert not (tmp_path / "nope.fsbak").exists()


def test_truncated_archive_is_rejected(tmp_path: Path, backup_key: str) -> None:
    short = tmp_path / "short.fsbak"
    short.write_bytes(b"FSBKUP01" + b"x" * 10)
    with pytest.raises(ValueError, match="truncated"):
        decrypt_archive(short, verify_only=True)


def test_verify_mode_rejects_output_destination(
    tmp_path: Path, backup_key: str,
) -> None:
    source = tmp_path / "source.dump"
    source.write_bytes(b"test")
    encrypted = tmp_path / "secure.fsbak"
    encrypt_archive(source, encrypted)
    with pytest.raises(ValueError, match="Verify mode"):
        decrypt_archive(
            encrypted, tmp_path / "out.dump", verify_only=True,
        )
