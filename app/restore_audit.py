"""Explicit, isolated S3 encrypted-backup recovery drill.

Dry-run needs no database, cloud client, or key. An opted-in execution
downloads authenticated ciphertext, decrypts into a private temporary
directory, restores ONLY to a new footscore_restore_* database, then
checks the restored schema and critical table accessibility.

No production database is modified, no archive is deleted, and no
restore test database is automatically dropped.
"""
import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from app.backup_crypto import _key, decrypt_archive
from app.backup_remote import fetch_and_verify_encrypted_archive, validate_destination
from app.services.recovery_metrics import recovery_measurements

ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = ROOT / "scripts" / "postgres_restore_drill.sh"
CRITICAL_TABLES = (
    "matches", "users", "live_updates", "auth_one_time_tokens",
    "email_deliveries", "backup_alert_states",
)
TARGET_RE = re.compile(r"footscore_restore_[A-Za-z0-9_]{1,30}\Z")
KEY_RE = re.compile(
    r"(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/"
    r"(?P<stamp>\d{8}T\d{12}Z)-[0-9a-f]{32}\.fsbak\Z"
)


def validate_drill_target(target: str, source: str | None) -> None:
    if not TARGET_RE.fullmatch(target) or len(target) > 48 or target == source:
        raise ValueError("Use a NEW, distinct footscore_restore_* database name")


def validate_archive_key(prefix: str, key: str) -> None:
    from datetime import datetime

    m = KEY_RE.fullmatch(key[len(prefix) + 1:]) if key.startswith(prefix + "/") else None
    if not m:
        raise ValueError("Archive key must match a FootScore encrypted backup filename")
    try:
        created = datetime.strptime(m["stamp"], "%Y%m%dT%H%M%S%fZ")
    except ValueError as exc:
        raise ValueError("Invalid archive timestamp") from exc
    if created.strftime("%Y/%m/%d") != "/".join(
        m[group] for group in ("y", "m", "d")
    ):
        raise ValueError("Archive key date does not match its timestamp")


def _pg_configuration() -> None:
    for name in ("PGHOST", "PGUSER", "PGDATABASE"):
        if not os.environ.get(name):
            raise ValueError("Set PGHOST, PGUSER and source PGDATABASE")
    if os.environ["PGDATABASE"].startswith("footscore_restore_"):
        raise ValueError("Source PGDATABASE must not be a disposable restore target")


def audit_restored_database(target: str) -> dict:
    """Run read-only SQL against the isolated restored database.

    The report contains only schema/row counts, not account or player data.
    A correct schema does not prove all historic rows or API functionality.
    """
    import psycopg
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    validate_drill_target(target, os.environ.get("PGDATABASE"))
    _pg_configuration()
    started = time.monotonic()
    cfg = Config(str(ROOT / "alembic.ini"))
    expected = ScriptDirectory.from_config(cfg).get_current_head()
    if not expected:
        raise RuntimeError("Alembic has no single current head")
    kwargs = {
        "host": os.environ["PGHOST"], "user": os.environ["PGUSER"],
        "dbname": target, "connect_timeout": 5,
    }
    if os.environ.get("PGPORT"):
        kwargs["port"] = os.environ["PGPORT"]
    with psycopg.connect(**kwargs) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revisions = cursor.fetchall()
            if len(revisions) != 1 or revisions[0][0] != expected:
                raise RuntimeError("Restored Alembic schema is not at current head")
            counts = {}
            for table in CRITICAL_TABLES:
                # Table names are constants in this module, never user input.
                cursor.execute(f"SELECT COUNT(*) FROM public.{table}")  # noqa: S608
                counts[table] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM live_updates l "
                           "LEFT JOIN matches m ON m.id = l.match_id "
                           "WHERE m.id IS NULL")
            orphan_updates = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM email_deliveries d "
                           "LEFT JOIN auth_one_time_tokens t ON t.id = d.token_id "
                           "WHERE t.id IS NULL")
            orphan_email = cursor.fetchone()[0]
            if orphan_updates or orphan_email:
                raise RuntimeError("Restored database contains orphaned outbox rows")
            cursor.execute(
                "SELECT COUNT(*) FROM pg_constraint WHERE contype = 'f' "
                "AND NOT convalidated"
            )
            invalid_fk_constraints = cursor.fetchone()[0]
            if invalid_fk_constraints:
                raise RuntimeError("Restored database has unvalidated foreign keys")
    return {
        "target": target, "schema_matches_current_head": True,
        "alembic_revision": expected, "row_counts": counts,
        "orphan_outbox_rows": 0,
        "foreign_key_constraints_validated": True,
        "critical_table_count": len(CRITICAL_TABLES),
        "audit_elapsed_seconds": round(time.monotonic() - started, 3),
        "encrypted_archive_authenticated": None,
        "application_functionality_verified": False,
        "source_completeness_verified": False, "rpo_rto_slo_verified": False,
    }


def restore_once(
    *, bucket: str, key: str, target: str, prefix: str = "footscore",
    execute: bool = False, s3=None, workspace: Path | None = None,
    runner=None, auditor=None, max_drill_seconds: int | None = None,
) -> dict:
    """Conduct an explicit, non-overwriting encrypted remote restore drill."""
    validate_destination(bucket, prefix)
    validate_archive_key(prefix, key)
    validate_drill_target(target, os.environ.get("PGDATABASE"))
    if max_drill_seconds is not None and not 1 <= max_drill_seconds <= 3600:
        raise ValueError("Duration budget must be between 1 and 3600 seconds")
    if not execute:
        return {
            "dry_run": True, "remote_download": False,
            "database_created": False,
            "note": "No cloud, key, PostgreSQL or filesystem operation attempted",
        }
    _pg_configuration()
    _key()
    if workspace is not None:
        workspace = Path(workspace)
        if workspace.is_symlink() or not workspace.is_dir():
            raise ValueError("Workspace must be a trusted non-symlink directory")
    if not RESTORE_SCRIPT.is_file():
        raise RuntimeError("Restore helper unavailable")
    if s3 is None:
        import boto3

        s3 = boto3.client("s3")
    if runner is None:
        runner = subprocess.run
    if auditor is None:
        auditor = audit_restored_database

    started = time.monotonic()
    phase_seconds = {}
    with tempfile.TemporaryDirectory(prefix="footscore-restore-drill-", dir=workspace) as name:
        directory = Path(name)
        encrypted = directory / "downloaded.fsbak"
        plaintext = directory / "restore-input.dump"
        phase_started = time.monotonic()
        fetch_and_verify_encrypted_archive(
            s3, bucket=bucket, key=key, destination=encrypted,
        )
        phase_seconds["download_authenticate"] = time.monotonic() - phase_started
        phase_started = time.monotonic()
        decrypt_archive(encrypted, plaintext)
        phase_seconds["decrypt"] = time.monotonic() - phase_started
        phase_started = time.monotonic()
        try:
            runner(
                ["sh", str(RESTORE_SCRIPT), str(plaintext), target],
                check=True, timeout=900,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(
                "Isolated restore failed; inspect the disposable target before cleanup"
            ) from exc
        phase_seconds["postgres_restore"] = time.monotonic() - phase_started
        # A completed pg_restore is not enough: check the target independently.
        phase_started = time.monotonic()
        report = auditor(target)
        if report.get("schema_matches_current_head") is not True:
            raise RuntimeError("Independent restored schema audit did not pass")
        phase_seconds["schema_audit"] = time.monotonic() - phase_started
    evidence = recovery_measurements(
        phase_seconds, elapsed_seconds=time.monotonic() - started,
        budget_seconds=max_drill_seconds,
    )
    return {
        "dry_run": False, "remote_download": True,
        "database_created": True, "cryptographic_integrity_verified": True,
        "restored_schema_checked": True, "audit": report,
        "elapsed_seconds": evidence["total_elapsed_seconds"],
        "measurements": evidence,
        "rpo_rto_slo_verified": False,
        "note": "Disposable target is intentionally retained for operator inspection",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FootScore isolated encrypted backup restore audit"
    )
    sub = parser.add_subparsers(dest="operation", required=True)
    run = sub.add_parser("restore", help="Optional S3 download and NEW database restore")
    run.add_argument("--bucket", required=True)
    run.add_argument("--prefix", default="footscore")
    run.add_argument("--key", required=True)
    run.add_argument("--target", required=True)
    run.add_argument("--workspace", type=Path)
    run.add_argument("--execute", action="store_true")
    run.add_argument(
        "--max-drill-seconds", type=int,
        help="Optional 1..3600 second local drill duration budget, NOT an RTO",
    )
    check = sub.add_parser("verify", help="Read-only audit of an existing restore database")
    check.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        if args.operation == "restore":
            report = restore_once(
                bucket=args.bucket, prefix=args.prefix, key=args.key,
                target=args.target, execute=args.execute, workspace=args.workspace,
                max_drill_seconds=args.max_drill_seconds,
            )
        else:
            report = audit_restored_database(args.target)
    except Exception:
        # Connection strings, S3 object keys and cloud errors can contain
        # sensitive details. Avoid outputting raw exception messages.
        parser.exit(1, "Backup restore audit failed; no source database was modified\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
