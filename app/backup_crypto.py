"""Streaming authenticated encryption for PostgreSQL custom-format backups.

Archive format: b"FSBKUP01" + 12-byte random nonce + AES-256-GCM ciphertext
+ 16-byte authentication tag. The format is FootScore-specific and requires
a separately managed 32-byte backup encryption key.

Only verified plaintext can be published at the output path. Decrypting
untrusted ciphertext first writes to a private temporary file, which is
removed if authentication fails. This does not eliminate remnants of
temporary plaintext from storage, snapshots or filesystem journals.
"""
import argparse
import os
import stat
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"FSBKUP01"
NONCE_BYTES = 12
TAG_BYTES = 16
BUFFER_BYTES = 1024 * 1024


def _key() -> bytes:
    raw = os.environ.get("FOOTSCORE_BACKUP_KEY", "")
    if len(raw) != 64:
        raise ValueError("Configure a separate 32-byte FOOTSCORE_BACKUP_KEY as 64 hex digits")
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError("FOOTSCORE_BACKUP_KEY must contain only hexadecimal digits") from exc
    if len(key) != 32:
        raise ValueError("Invalid backup encryption key size")
    return key


def _input(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Input must be an existing, non-symlink regular archive")


def _destination(path: Path, source: Path) -> None:
    if path == source or os.path.lexists(path):
        raise ValueError("Refusing to overwrite input or existing output")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("Output must have an existing non-symlink parent directory")


def _temporary(path: Path) -> tuple[int, Path]:
    fd, name = tempfile.mkstemp(prefix=".footscore-backup-", dir=str(path.parent))
    return fd, Path(name)


def _publish(tmp: Path, dest: Path) -> None:
    # The hard link is atomic and fails if another process creates dest.
    os.link(tmp, dest)


def encrypt_archive(source: Path, destination: Path) -> None:
    """Encrypt a large database archive without loading it into memory."""
    source, destination = Path(source), Path(destination)
    _input(source)
    _destination(destination, source)
    secret = _key()
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(secret), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC + nonce)
    fd, tmp = _temporary(destination)
    try:
        with open(source, "rb") as src, os.fdopen(fd, "wb") as out:
            out.write(MAGIC + nonce)
            while chunk := src.read(BUFFER_BYTES):
                out.write(encryptor.update(chunk))
            out.write(encryptor.finalize())
            out.write(encryptor.tag)
            out.flush()
            os.fsync(out.fileno())
        _publish(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def decrypt_archive(
    source: Path, destination: Path | None = None, *, verify_only: bool = False,
) -> None:
    """Authenticate before publishing plaintext; verify can stream with no output."""
    source = Path(source)
    _input(source)
    if not verify_only:
        if destination is None:
            raise ValueError("Provide --output when decrypting an archive")
        destination = Path(destination)
        _destination(destination, source)
    elif destination is not None:
        raise ValueError("Verify mode must not have an output destination")
    secret = _key()
    total = source.stat().st_size
    header_size = len(MAGIC) + NONCE_BYTES
    if total < header_size + TAG_BYTES:
        raise ValueError("Invalid or truncated encrypted backup")
    with open(source, "rb") as src:
        header = src.read(header_size)
        if header[:len(MAGIC)] != MAGIC:
            raise ValueError("Unrecognized encrypted backup format")
        src.seek(total - TAG_BYTES)
        tag = src.read(TAG_BYTES)
        nonce = header[len(MAGIC):]
        decryptor = Cipher(algorithms.AES(secret), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        src.seek(header_size)
        left = total - header_size - TAG_BYTES
        if verify_only:
            while left:
                block = src.read(min(BUFFER_BYTES, left))
                if not block:
                    raise ValueError("Truncated backup ciphertext")
                decryptor.update(block)
                left -= len(block)
            decryptor.finalize()
            return

        assert destination is not None
        fd, tmp = _temporary(destination)
        try:
            with os.fdopen(fd, "wb") as out:
                while left:
                    block = src.read(min(BUFFER_BYTES, left))
                    if not block:
                        raise ValueError("Truncated backup ciphertext")
                    out.write(decryptor.update(block))
                    left -= len(block)
                out.write(decryptor.finalize())
                out.flush()
                os.fsync(out.fileno())
            _publish(tmp, destination)
        finally:
            tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FootScore streaming AES-256-GCM backup encryption"
    )
    parser.add_argument("command", choices=("encrypt", "decrypt", "verify"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "encrypt":
            if args.output is None:
                raise ValueError("Encrypt requires --output")
            encrypt_archive(args.input, args.output)
        elif args.command == "decrypt":
            decrypt_archive(args.input, args.output)
        else:
            decrypt_archive(args.input, verify_only=True)
    except (ValueError, InvalidTag, OSError) as exc:
        # No key, backup bytes, or path contents should appear in errors.
        if isinstance(exc, InvalidTag):
            parser.error("Backup integrity verification failed")
        parser.error(str(exc))
    print("Backup operation completed successfully; protect encryption keys.")


if __name__ == "__main__":
    main()
