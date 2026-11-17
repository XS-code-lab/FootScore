"""Upload and independently verify *encrypted* FootScore backups via S3 APIs.

All writes use S3 conditional If-None-Match: *, so a prior object is never
silently replaced. This implementation deliberately caps objects at 5 GiB
(single PutObject); multipart upload requires a different safe design.
"""
import base64
import hashlib
import re
from pathlib import Path

from app.backup_crypto import MAGIC, decrypt_archive

MAX_BYTES = 5 * 1024**3
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,180}$")


def validate_destination(bucket: str, prefix: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", bucket):
        raise ValueError("Invalid backup bucket name")
    if (
        not NAME_PATTERN.fullmatch(prefix)
        or prefix.startswith("/") or "//" in prefix or ".." in prefix
        or prefix.endswith("/")
    ):
        raise ValueError("Unsafe backup object prefix")


def _digest(path: Path) -> tuple[str, str, int]:
    hash_value = hashlib.sha256()
    size = 0
    with path.open("rb") as src:
        while chunk := src.read(1024 * 1024):
            hash_value.update(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("Backup exceeds safe single-object 5 GiB limit")
    return (
        hash_value.hexdigest(),
        base64.b64encode(hash_value.digest()).decode("ascii"),
        size,
    )


def store_encrypted_archive(
    s3, *, path: Path, bucket: str, key: str,
) -> dict:
    """Upload only GCM-encrypted bytes; compare server response + HEAD metadata."""
    if not NAME_PATTERN.fullmatch(key) or ".." in key or "//" in key:
        raise ValueError("Unsafe backup object key")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Encrypted archive must be a regular, non-symlink file")
    with path.open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise ValueError("Refusing to upload an unencrypted PostgreSQL archive")

    # Authentication uses private backup key and rejects truncated/tampered data.
    decrypt_archive(path, verify_only=True)
    sha_hex, checksum, size = _digest(path)
    with path.open("rb") as src:
        response = s3.put_object(
            Bucket=bucket, Key=key, Body=src,
            ContentType="application/octet-stream",
            ContentLength=size, ChecksumSHA256=checksum,
            IfNoneMatch="*",
            Metadata={"footscore-sha256": sha_hex, "format": "fsbkup01"},
        )
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if status not in (200, 201):
        raise RuntimeError("Remote object upload was not confirmed")
    obj = s3.head_object(Bucket=bucket, Key=key)
    if (
        obj.get("ContentLength") != size
        or obj.get("Metadata", {}).get("footscore-sha256") != sha_hex
        or obj.get("Metadata", {}).get("format") != "fsbkup01"
    ):
        raise RuntimeError("Remote backup metadata verification failed")
    # HEAD metadata is only an upload check, not a cryptographic download test.
    return {"uploaded": True, "bytes": size, "sha256": sha_hex, "object_key": key}


def fetch_and_verify_encrypted_archive(
    s3, *, bucket: str, key: str, destination: Path,
) -> None:
    """Download into a new private path and verify GCM authenticity locally."""
    import os
    import tempfile

    if (
        os.path.lexists(destination)
        or not destination.parent.is_dir()
        or destination.parent.is_symlink()
    ):
        raise ValueError("Refusing to overwrite or use an unsafe download destination")
    fd, temp_name = tempfile.mkstemp(
        prefix=".footscore-remote-", dir=str(destination.parent),
    )
    try:
        with os.fdopen(fd, "wb") as out:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"]
            try:
                total = 0
                while chunk := body.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Remote backup exceeds safe size limit")
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            finally:
                body.close()
        decrypt_archive(Path(temp_name), verify_only=True)
        os.link(temp_name, destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)
