"""The runtime privilege policy must fail closed on any admin privilege."""
from copy import deepcopy

import pytest

from app.db_role_audit import summarize_runtime_permissions


def safe_flags():
    return {
        "superuser": False,
        "createdb": False,
        "createrole": False,
        "replication": False,
        "bypassrls": False,
        "login": True,
        "schema_usage": True,
        "schema_create": False,
        "db_create": False,
        "different_migration_role": True,
        "migration_role_member": False,
        "table_count": 21,
        "table_owned": 0,
        "table_missing_crud": 0,
        "table_unsafe_privileges": 0,
        "sequence_count": 19,
        "sequence_missing_usage": 0,
    }


def test_good_runtime_has_all_crud_but_no_schema_admin_powers():
    report = summarize_runtime_permissions(safe_flags())
    assert report["runtime_role_policy_passed"] is True
    assert report["table_count"] == 21
    assert report["sequences_missing_usage"] == 0
    assert report["migrator_is_distinct"]
    assert not report["role_names_or_credentials_exported"]
    assert not report["cluster_global_roles_restored"]
    assert not report["production_security_audit_completed"]


@pytest.mark.parametrize("key", [
    "superuser", "createdb", "createrole", "replication", "bypassrls",
    "schema_create", "db_create", "migration_role_member",
])
def test_any_excessive_database_privilege_fails_gate(key):
    raw = safe_flags()
    raw[key] = True
    report = summarize_runtime_permissions(raw)
    assert not report["runtime_role_policy_passed"]


@pytest.mark.parametrize("key", ["login", "schema_usage", "different_migration_role"])
def test_missing_required_runtime_properties_fail_gate(key):
    raw = safe_flags()
    raw[key] = False
    assert not summarize_runtime_permissions(raw)["runtime_role_policy_passed"]


@pytest.mark.parametrize("key", [
    "table_owned", "table_missing_crud",
    "table_unsafe_privileges", "sequence_missing_usage",
])
def test_owner_or_excessive_and_missing_grants_are_rejected(key):
    raw = safe_flags()
    raw[key] = 1
    report = summarize_runtime_permissions(raw)
    assert report["runtime_role_policy_passed"] is False
    assert "secret" not in str(report)


@pytest.mark.parametrize("key", ["table_count", "sequence_count"])
def test_empty_catalog_never_passes(key):
    raw = safe_flags()
    raw[key] = 0
    assert not summarize_runtime_permissions(raw)["runtime_role_policy_passed"]


@pytest.mark.parametrize(("key", "bad"), [
    ("superuser", None),
    ("superuser", "false"),
    ("table_count", -1),
    ("sequence_count", 1.5),
    ("table_missing_crud", True),
])
def test_invalid_privilege_snapshot_does_not_claim_security(key, bad):
    raw = safe_flags()
    raw[key] = bad
    with pytest.raises(ValueError):
        summarize_runtime_permissions(raw)


def test_missing_catalog_fields_fail_closed():
    data = deepcopy(safe_flags())
    del data["migration_role_member"]
    with pytest.raises(ValueError):
        summarize_runtime_permissions(data)
