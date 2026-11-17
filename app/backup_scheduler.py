"""Opt-in encrypted PostgreSQL dump and S3-compatible off-site upload.

This command never uploads anything unless --execute is supplied. No remote
deletion or automatic retention runs are provided by this module.
"""
import argparse
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.backup_crypto import _key, decrypt_archive, encrypt_archive
from app.backup_remote import store_encrypted_archive, validate_destination

LOG = logging.getLogger(__name__)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "postgres_backup.sh"


def backup_once(
    *, bucket: str, prefix: str = "footscore", execute: bool = False,
    workspace: Path | None = None, s3=None,
    now: datetime | None = None,
) -> dict:
    """Explicit one-shot backup; only encrypted archives leave private workspace."""
    validate_destination(bucket, prefix)
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Backup clock must have timezone information")
    now = now.astimezone(timezone.utc)
    if not execute:
        return {
            "dry_run": True, "remote_upload": False,
            "encryption": "AES-256-GCM",
            "note": "No database or cloud connection was attempted",
        }

    _key()  # Fail before opening a database connection if key is missing.
    for var in ("PGHOST", "PGUSER", "PGDATABASE"):
        if not os.environ.get(var):
            raise ValueError(f"Required PostgreSQL connection setting: {var}")
    if not SCRIPT.is_file():
        raise RuntimeError("PostgreSQL backup script is unavailable")
    if workspace is not None:
        workspace = Path(workspace)
        if workspace.is_symlink() or not workspace.is_dir():
            raise ValueError("Workspace must be an existing non-symlink directory")

    # boto3 is an optional dependency. No boto3 import or cloud request in dry run.
    if s3 is None:
        import boto3

        s3 = boto3.client("s3")

    remote_key = (
        f"{prefix}/{now:%Y/%m/%d}/"
        f"{now:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex}.fsbak"
    )
    start = time.monotonic()
    with tempfile.TemporaryDirectory(
        prefix="footscore-db-backup-", dir=workspace,
    ) as dirname:
        folder = Path(dirname)
        plain = folder / "backup.dump"
        encrypted = folder / "backup.fsbak"
        # Do not capture and log raw pg_dump errors: connection strings can
        # contain secrets. Propagate a safe, generic operational failure.
        try:
            subprocess.run(
                ["sh", str(SCRIPT), str(plain)],
                check=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=900,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError("Database backup command failed") from exc
        encrypt_archive(plain, encrypted)
        plain.unlink()
        decrypt_archive(encrypted, verify_only=True)
        stored = store_encrypted_archive(
            s3, path=encrypted, bucket=bucket, key=remote_key,
        )
    return {
        "dry_run": False,
        "remote_upload": True,
        "object_key": stored["object_key"],
        "bytes": stored["bytes"],
        "sha256": stored["sha256"],
        "duration_seconds": round(time.monotonic() - start, 3),
        "remote_download_restore_verified": False,
        "note": "Upload HEAD check is not a restore drill",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Encrypted PostgreSQL S3 backup")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="footscore")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-hours", type=int, default=24)
    args = parser.parse_args()
    if args.watch and not args.execute:
        parser.error("Scheduled backup requires explicit --execute")
    if args.watch and not 6 <= args.interval_hours <= 168:
        parser.error("Interval must be between 6 hours and 7 days")
    try:
        validate_destination(args.bucket, args.prefix)
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            result = backup_once(
                bucket=args.bucket, prefix=args.prefix,
                execute=args.execute, workspace=args.workspace,
            )
            # Object keys are operational metadata; log aggregated result only.
            LOG.info(
                "Backup cycle: upload=%s bytes=%s duration=%s",
                result["remote_upload"], result.get("bytes"), result.get("duration_seconds"),
            )
            if not args.watch:
                print(json.dumps(result, indent=2))
        except Exception:
            # Never log exceptions: boto3/pg connection errors may contain
            # request parameters, bucket names, or other sensitive data.
            LOG.error("Backup operation failed (details suppressed)")
            if not args.watch:
                raise SystemExit(1) from None
        if not args.watch:
            return
        time.sleep(args.interval_hours * 3600)


if __name__ == "__main__":
    main()
