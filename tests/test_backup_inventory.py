"""Read-only remote backup inventory: strict, bounded, no DeleteObject."""
from datetime import datetime, timedelta, timezone

import pytest

from app.backup_inventory import BackupInventoryError, inspect_remote_backups


NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
BUCKET = "footscore-test-bucket"


def make_object(
    days_ago: int, *, uid: str, prefix: str = "footscore",
    storage: str = "STANDARD",
) -> dict:
    created = NOW - timedelta(days=days_ago)
    name = (
        f"{prefix}/{created:%Y/%m/%d}/"
        f"{created:%Y%m%dT%H%M%S%fZ}-{uid}.fsbak"
    )
    return {
        "Key": name, "LastModified": created + timedelta(seconds=2),
        "Size": 2048, "StorageClass": storage,
    }


class FakeInventoryS3:
    def __init__(self, pages: list[dict]):
        self.pages = pages
        self.calls = []
        self.mutations = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["Bucket"] == BUCKET
        assert kwargs["Prefix"] == "footscore/"
        assert kwargs["MaxKeys"] == 1000
        idx = int(kwargs.get("ContinuationToken", "0"))
        return self.pages[idx]

    def delete_object(self, **kwargs):
        self.mutations.append(("delete", kwargs))
        raise AssertionError("Retention inventory must never delete objects")


def test_recent_backup_and_conservative_retention_plan_never_mutates():
    items = [
        make_object(0, uid="a" * 32),
        make_object(1, uid="b" * 32),
        make_object(2, uid="c" * 32),
        make_object(40, uid="d" * 32),
        make_object(90, uid="e" * 32),
        make_object(99, uid="f" * 32, prefix="other"),
        {"Key": "footscore/README", "Size": 1000, "LastModified": NOW},
    ]
    fake = FakeInventoryS3([
        {"Contents": items[:4], "IsTruncated": True, "NextContinuationToken": "1"},
        {"Contents": items[4:], "IsTruncated": False},
    ])
    result = inspect_remote_backups(
        fake, bucket=BUCKET, now=NOW, retention_days=30, keep_latest=3,
    )
    assert result["source"] == "s3_listing_metadata_only"
    assert result["pages_scanned"] == 2
    assert result["objects_scanned"] == 7
    assert result["matched_archives"] == 5
    assert result["ignored_objects"] == 2
    assert result["freshness"]["state"] == "recent_unverified"
    assert not result["freshness"]["cryptographic_integrity_verified"]
    assert not result["freshness"]["restore_verified"]
    assert result["retention_plan"]["dry_run"]
    assert result["retention_plan"]["delete_calls"] == 0
    assert result["retention_plan"]["candidate_count"] == 2
    candidate_keys = [item["key"] for item in result["retention_plan"]["candidates"]]
    assert items[3]["Key"] in candidate_keys and items[4]["Key"] in candidate_keys
    assert all(item["Key"] not in candidate_keys for item in items[:3])
    assert fake.calls[1]["ContinuationToken"] == "1"
    assert fake.mutations == []


def test_protect_latest_three_even_when_all_archives_are_old():
    backups = [make_object(n, uid=f"{n:032x}") for n in (40, 60, 80, 110)]
    fake = FakeInventoryS3([{"Contents": backups, "IsTruncated": False}])
    result = inspect_remote_backups(fake, bucket=BUCKET, now=NOW)
    assert result["freshness"]["state"] == "stale"
    assert result["retention_plan"]["candidate_count"] == 1
    assert result["retention_plan"]["candidates"][0]["key"] == backups[-1]["Key"]
    assert fake.mutations == []


def test_missing_backup_and_unknown_objects_are_not_called_healthy():
    wrong_shape = [
        make_object(0, uid="a" * 32, storage="GLACIER"),
        make_object(0, uid="b" * 32, prefix="other"),
        {
            **make_object(0, uid="c" * 32),
            "Key": "footscore/2026/11/17/20261118T120000000000Z-" + "c" * 32 + ".fsbak",
        },
        {**make_object(0, uid="d" * 32), "Size": 10},
        {**make_object(0, uid="e" * 32), "LastModified": NOW + timedelta(days=1)},
    ]
    fake = FakeInventoryS3([{"Contents": wrong_shape, "IsTruncated": False}])
    result = inspect_remote_backups(fake, bucket=BUCKET, now=NOW)
    assert result["matched_archives"] == 0
    assert result["freshness"]["state"] == "missing"
    assert result["freshness"]["newest_candidate_age_seconds"] is None
    assert result["retention_plan"]["candidate_count"] == 0


@pytest.mark.parametrize("page", [
    {"Contents": "not a list", "IsTruncated": False},
    {"Contents": [{}], "IsTruncated": True},
    {"Contents": [{}], "IsTruncated": True, "NextContinuationToken": ""},
    {"Contents": [None], "IsTruncated": False},
    {"Contents": [], "IsTruncated": "false"},
    {"Contents": []},
])
def test_bad_page_fails_closed_without_false_success(page):
    fake = FakeInventoryS3([page])
    with pytest.raises(BackupInventoryError):
        inspect_remote_backups(fake, bucket=BUCKET, now=NOW)
    assert not fake.mutations


def test_repeated_continuation_is_rejected():
    fake = FakeInventoryS3([
        {"Contents": [], "IsTruncated": True, "NextContinuationToken": "1"},
        {"Contents": [], "IsTruncated": True, "NextContinuationToken": "1"},
    ])
    with pytest.raises(BackupInventoryError, match="continuation"):
        inspect_remote_backups(fake, bucket=BUCKET, now=NOW)
    assert len(fake.calls) == 2


def test_bounded_listing_rejects_more_than_ten_thousand_objects():
    fake = FakeInventoryS3([
        {"Contents": [{}] * 1000, "IsTruncated": True,
         "NextContinuationToken": str(i + 1)}
        for i in range(12)
    ])
    with pytest.raises(BackupInventoryError, match="object cap"):
        inspect_remote_backups(fake, bucket=BUCKET, now=NOW)
    assert len(fake.calls) == 11


def test_max_page_limit_is_fail_closed():
    fake = FakeInventoryS3([
        {"Contents": [], "IsTruncated": True, "NextContinuationToken": str(i + 1)}
        for i in range(20)
    ])
    with pytest.raises(BackupInventoryError, match="page cap"):
        inspect_remote_backups(fake, bucket=BUCKET, now=NOW)
    assert len(fake.calls) == 20


@pytest.mark.parametrize("settings", [
    {"max_age_hours": 0},
    {"max_age_hours": 745},
    {"retention_days": 29},
    {"keep_latest": 2},
    {"keep_latest": 101},
    {"now": NOW.replace(tzinfo=None)},
])
def test_invalid_policy_does_not_contact_s3(settings):
    fake = FakeInventoryS3([])
    with pytest.raises(ValueError):
        inspect_remote_backups(fake, bucket=BUCKET, now=NOW, **{
            k: v for k, v in settings.items() if k != "now"
        }) if "now" not in settings else inspect_remote_backups(
            fake, bucket=BUCKET, **settings,
        )
    assert not fake.calls




def test_duplicate_archive_between_pages_fails_closed():
    same = make_object(0, uid="a" * 32)
    fake = FakeInventoryS3([
        {"Contents": [same], "IsTruncated": True, "NextContinuationToken": "1"},
        {"Contents": [same], "IsTruncated": False},
    ])
    with pytest.raises(BackupInventoryError, match="Duplicate"):
        inspect_remote_backups(fake, bucket=BUCKET, now=NOW)
    assert fake.mutations == []


def test_due_to_unknown_cloud_state_no_deletion_ever_runs():
    fake = FakeInventoryS3([
        {"Contents": [make_object(90, uid=f"{n:032x}") for n in range(4)],
         "IsTruncated": False},
    ])
    result = inspect_remote_backups(
        fake, bucket=BUCKET, now=NOW, retention_days=30, keep_latest=3,
    )
    assert result["retention_plan"]["candidate_count"] == 1
    assert result["retention_plan"]["delete_calls"] == 0
    assert fake.mutations == []

def test_provider_listing_failure_propagates_instead_of_empty_success():
    class BrokenS3:
        def list_objects_v2(self, **kwargs):
            raise OSError("synthetic cloud outage")
    with pytest.raises(OSError, match="cloud outage"):
        inspect_remote_backups(BrokenS3(), bucket=BUCKET, now=NOW)
