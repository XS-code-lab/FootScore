"""CI-only mock S3 transfer of a REAL synthetic PostgreSQL archive.

The source archive comes from actual pg_dump on disposable PostgreSQL.
No cloud credentials, actual bucket, or provider network calls are involved.
"""
import argparse
import io
import os
from datetime import datetime, timezone
from pathlib import Path

from app.backup_inventory import inspect_remote_backups
from app.restore_audit import restore_once

from app.backup_remote import (
    fetch_and_verify_encrypted_archive, store_encrypted_archive,
)

BUCKET = "footscore-ci-mock-only"
CI_CREATED_AT = datetime.now(timezone.utc)
KEY = (
    f"footscore/{CI_CREATED_AT:%Y/%m/%d}/"
    f"{CI_CREATED_AT:%Y%m%dT%H%M%S%fZ}-{'a' * 32}.fsbak"
)


class FakeS3:
    def __init__(self):
        self.items = {}
        self.calls = []

    def put_object(self, **params):
        assert params["IfNoneMatch"] == "*"
        assert params["Bucket"] == BUCKET
        assert params["Key"] == KEY
        assert (BUCKET, KEY) not in self.items
        data = params["Body"].read()
        assert data.startswith(b"FSBKUP01")
        assert len(data) == params["ContentLength"]
        self.items[(BUCKET, KEY)] = (data, params["Metadata"])
        self.calls.append("put")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def head_object(self, *, Bucket, Key):
        assert (Bucket, Key) in self.items
        self.calls.append("head")
        data, metadata = self.items[(Bucket, Key)]
        return {"ContentLength": len(data), "Metadata": metadata}

    def list_objects_v2(self, **params):
        assert params["Bucket"] == BUCKET
        assert params["Prefix"] == "footscore/"
        self.calls.append("list")
        data, _ = self.items[(BUCKET, KEY)]
        return {
            "Contents": [{
                "Key": KEY, "Size": len(data),
                "LastModified": datetime.now(timezone.utc),
                "StorageClass": "STANDARD",
            }],
            "IsTruncated": False,
        }

    def get_object(self, *, Bucket, Key):
        self.calls.append("get")
        return {"Body": io.BytesIO(self.items[(Bucket, Key)][0])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--encrypted", type=Path, required=True)
    parser.add_argument("--downloaded", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("FOOTSCORE_INTEGRATION_TESTS") != "1":
        parser.error("Requires isolated CI integration environment")
    if args.downloaded.exists():
        parser.error("Refusing existing download destination")
    s3 = FakeS3()
    result = store_encrypted_archive(
        s3, path=args.encrypted, bucket=BUCKET, key=KEY,
    )
    assert result["uploaded"] and result["bytes"] > 36
    fetch_and_verify_encrypted_archive(
        s3, bucket=BUCKET, key=KEY, destination=args.downloaded,
    )
    report = inspect_remote_backups(s3, bucket=BUCKET, prefix="footscore")
    assert report["complete_listing"]
    assert report["freshness"]["state"] == "recent_unverified"
    assert report["retention_plan"]["delete_calls"] == 0
    assert report["retention_plan"]["candidate_count"] == 0
    assert s3.calls == ["put", "head", "get", "list"]
    assert args.downloaded.read_bytes() == args.encrypted.read_bytes()

    # Exercise the recovery orchestrator using the actual PGDMP archive.
    # CI subsequently runs the *real* postgres:16 pg_restore and SQL audit.
    preview = restore_once(
        bucket=BUCKET, key=KEY, target="footscore_restore_ci_routing",
    )
    assert preview["dry_run"] and not preview["remote_download"]

    def check_archive(argv, **kwargs):
        assert argv[0] == "sh"
        assert argv[-1] == "footscore_restore_ci_routing"
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 900
        with Path(argv[2]).open("rb") as stream:
            assert stream.read(5) == b"PGDMP"

    recovered = restore_once(
        bucket=BUCKET, key=KEY, target="footscore_restore_ci_routing",
        execute=True, s3=s3, runner=check_archive,
        auditor=lambda target: {
            "target": target, "schema_matches_current_head": True,
            "note": "Full SQL checks execute separately against real pg_restore",
        },
        max_drill_seconds=900,
    )
    assert recovered["measurements"]["operator_drill_budget_met"] is True
    assert recovered["measurements"]["production_rto_verified"] is False
    assert len(recovered["measurements"]["phase_seconds"]) == 4
    assert recovered["cryptographic_integrity_verified"]
    assert recovered["audit"]["schema_matches_current_head"]
    assert s3.calls == ["put", "head", "get", "list", "get"]
    print("Mock S3 upload + authenticated download of real CI archive succeeded.")


if __name__ == "__main__":
    main()
