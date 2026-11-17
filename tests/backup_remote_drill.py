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
    print("Mock S3 upload + authenticated download of real CI archive succeeded.")


if __name__ == "__main__":
    main()
