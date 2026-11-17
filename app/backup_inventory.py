"""Read-only S3 backup inventory, freshness alerts and retention *proposals*.

Never calls DeleteObject or PutObject. ListObjectsV2 metadata and filenames
are NOT cryptographic evidence that a database can be restored. A restore
drill is still required. Require the exact FootScore-generated object shape.
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone

from app.backup_remote import validate_destination

MAX_PAGES = 20
MAX_OBJECTS = 10_000
INSTANT_STORAGE = {
    "STANDARD", "STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING",
    "GLACIER_IR", "EXPRESS_ONEZONE",
}


class BackupInventoryError(RuntimeError):
    """An incomplete or malformed listing must never produce a green report."""


def _archive_from_object(obj: dict, prefix: str, now: datetime) -> dict | None:
    name = obj.get("Key")
    if not isinstance(name, str):
        return None
    pattern = (
        rf"^{re.escape(prefix)}/(\d{{4}})/(\d{{2}})/(\d{{2}})/"
        r"(\d{8}T\d{12}Z)-[0-9a-f]{32}\.fsbak$"
    )
    match = re.fullmatch(pattern, name)
    if match is None:
        return None
    try:
        creation = datetime.strptime(match.group(4), "%Y%m%dT%H%M%S%fZ")
        creation = creation.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if creation.strftime("%Y/%m/%d") != "/".join(match.group(i) for i in (1, 2, 3)):
        return None
    last_modified = obj.get("LastModified")
    if (
        not isinstance(last_modified, datetime) or last_modified.tzinfo is None
        or last_modified.utcoffset() is None
    ):
        return None
    modified = last_modified.astimezone(timezone.utc)
    if creation > now + timedelta(minutes=5) or modified > now + timedelta(minutes=5):
        return None
    if modified < creation - timedelta(minutes=5):
        return None
    size = obj.get("Size")
    if type(size) is not int or size <= 36:
        return None
    if obj.get("StorageClass", "STANDARD") not in INSTANT_STORAGE:
        return None
    return {
        "key": name, "created_at": creation, "last_modified": modified,
        "encrypted_bytes": size,
    }


def inspect_remote_backups(
    s3, *, bucket: str, prefix: str = "footscore",
    now: datetime | None = None, max_age_hours: int = 30,
    retention_days: int = 30, keep_latest: int = 3,
) -> dict:
    """List a bounded S3 prefix, never modify remote data.

    Only objects with the generated key format and plausible metadata
    qualify. Listings are capped and pagination errors raise instead of
    returning an incomplete, falsely healthy result.
    """
    validate_destination(bucket, prefix)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Inventory clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if not 1 <= max_age_hours <= 24 * 31:
        raise ValueError("Freshness threshold must be 1..744 hours")
    if not 30 <= retention_days <= 3650:
        raise ValueError("Retention window must be 30..3650 days")
    if not 3 <= keep_latest <= 100:
        raise ValueError("At least three newest backups must be protected")

    objects_seen = 0
    matched = []
    pages = 0
    next_token = None
    used_tokens = set()
    seen_keys = set()
    while True:
        if pages >= MAX_PAGES:
            raise BackupInventoryError("Remote listing page cap exceeded")
        params = {
            "Bucket": bucket, "Prefix": f"{prefix}/", "MaxKeys": 1000,
        }
        if next_token is not None:
            params["ContinuationToken"] = next_token
        page = s3.list_objects_v2(**params)
        pages += 1
        if type(page.get("IsTruncated")) is not bool:
            raise BackupInventoryError("Missing or invalid truncation flag")
        items = page.get("Contents", [])
        if not isinstance(items, list):
            raise BackupInventoryError("Invalid remote listing response")
        objects_seen += len(items)
        if objects_seen > MAX_OBJECTS:
            raise BackupInventoryError("Remote listing object cap exceeded")
        for item in items:
            if not isinstance(item, dict):
                raise BackupInventoryError("Invalid object entry")
            archive = _archive_from_object(item, prefix, now)
            if archive is not None:
                if archive["key"] in seen_keys:
                    raise BackupInventoryError("Duplicate archive in remote listing")
                seen_keys.add(archive["key"])
                matched.append(archive)
        if not page["IsTruncated"]:
            break
        next_token = page.get("NextContinuationToken")
        if not isinstance(next_token, str) or not next_token or next_token in used_tokens:
            raise BackupInventoryError("Invalid or repeated continuation token")
        used_tokens.add(next_token)

    matched.sort(key=lambda item: (item["created_at"], item["key"]), reverse=True)
    latest = matched[0] if matched else None
    age_seconds = (
        max(0, int((now - latest["created_at"]).total_seconds()))
        if latest else None
    )
    status = (
        "missing" if latest is None
        else "stale" if age_seconds > max_age_hours * 3600
        else "recent_unverified"
    )

    cutoff = now - timedelta(days=retention_days)
    protected = {item["key"] for item in matched[:keep_latest]}
    candidates = [
        {
            "key": item["key"],
            "created_at": item["created_at"].isoformat(),
            "encrypted_bytes": item["encrypted_bytes"],
        }
        for item in matched
        if item["key"] not in protected
        and item["created_at"] < cutoff
        and item["last_modified"] < cutoff
    ]
    return {
        "source": "s3_listing_metadata_only",
        "observed_at": now.isoformat(),
        "prefix": prefix,
        "complete_listing": True,
        "pages_scanned": pages,
        "objects_scanned": objects_seen,
        "matched_archives": len(matched),
        "ignored_objects": objects_seen - len(matched),
        "freshness": {
            "state": status,
            "max_age_hours": max_age_hours,
            "newest_candidate_age_seconds": age_seconds,
            "newest_candidate_key": latest["key"] if latest else None,
            "cryptographic_integrity_verified": False,
            "restore_verified": False,
        },
        "retention_plan": {
            "dry_run": True,
            "delete_calls": 0,
            "retention_days": retention_days,
            "protected_latest": keep_latest,
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only S3 backup freshness and retention proposal"
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="footscore")
    parser.add_argument("--max-age-hours", type=int, default=30)
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--keep-latest", type=int, default=3)
    parser.add_argument(
        "--strict", action="store_true", help="Exit 2 if no recent backup candidate",
    )
    args = parser.parse_args()
    try:
        validate_destination(args.bucket, args.prefix)
        import boto3

        report = inspect_remote_backups(
            boto3.client("s3"), bucket=args.bucket, prefix=args.prefix,
            max_age_hours=args.max_age_hours,
            retention_days=args.retention_days, keep_latest=args.keep_latest,
        )
    except ValueError as exc:
        parser.error(str(exc))
    except Exception:
        # Botocore messages can include bucket identities or credentials.
        parser.exit(1, "Could not verify a complete remote backup listing\n")
    print(json.dumps(report, indent=2))
    if args.strict and report["freshness"]["state"] != "recent_unverified":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
