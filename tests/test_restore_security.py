"""CI-only restored security metadata comparison detects privilege drift."""
from copy import deepcopy

import pytest

from tests.compare_restore_security import _guard, summarize


def _example() -> dict:
    return {
        "objects": {
            "matches:r": {
                "owner": "footscore", "rls": False, "force_rls": False,
                "persistence": "p",
                "acl": (("footscore", "footscore", "SELECT", False),),
            },
            "matches_id_seq:S": {
                "owner": "footscore", "rls": False, "force_rls": False,
                "persistence": "p",
                "acl": (("footscore", "footscore", "USAGE", False),),
            },
        },
        "extensions": {"plpgsql": ("1.0", "pg_catalog")},
        "schemas": {
            "public": {
                "owner": "pg_database_owner",
                "acl": (("PUBLIC", "pg_database_owner", "USAGE", False),),
            },
        },
    }


def test_security_metadata_identical_without_exported_acl_details():
    original = _example()
    report = summarize(original, deepcopy(original))
    assert report["security_metadata_matches"]
    assert report["objects_checked"] == 2
    assert report["extensions_checked"] == 1
    assert report["schemas_checked"] == 1
    assert report["mismatched_objects"] == {
        "objects": [], "extensions": [], "schemas": [],
    }
    assert not report["acl_details_exported"]
    assert not report["role_secrets_exported"]
    assert not report["global_role_recreation_verified"]
    assert not report["production_least_privilege_verified"]


@pytest.mark.parametrize(("kind", "name", "field", "value"), [
    ("objects", "matches:r", "owner", "unexpected"),
    ("objects", "matches:r", "rls", True),
    ("objects", "matches:r", "force_rls", True),
    ("objects", "matches:r", "acl", (("PUBLIC", "footscore", "INSERT", False),)),
    ("objects", "matches:r", "persistence", "u"),
    ("schemas", "public", "owner", "unexpected"),
    ("schemas", "public", "acl", (("PUBLIC", "pg_database_owner", "CREATE", False),)),
])
def test_privilege_owner_rls_and_schema_drift_are_detected(
    kind, name, field, value,
):
    source = _example()
    changed = deepcopy(source)
    changed[kind][name][field] = value
    result = summarize(source, changed)
    assert not result["security_metadata_matches"]
    assert name in result["mismatched_objects"][kind]
    assert "unexpected" not in str(result)
    assert "INSERT" not in str(result)
    assert "CREATE" not in str(result)


def test_extension_version_and_missing_extension_are_detected():
    source = _example()
    changed = deepcopy(source)
    changed["extensions"]["plpgsql"] = ("0.0", "pg_catalog")
    assert not summarize(source, changed)["security_metadata_matches"]
    del changed["extensions"]["plpgsql"]
    result = summarize(source, changed)
    assert result["mismatched_objects"]["extensions"] == ["plpgsql"]


def test_extra_object_and_role_grant_are_detected():
    source = _example()
    changed = deepcopy(source)
    changed["objects"]["unexpected_table:r"] = changed["objects"]["matches:r"]
    assert summarize(source, changed)["mismatched_objects"]["objects"] == [
        "unexpected_table:r",
    ]


def test_security_verifier_refuses_non_disposable_targets(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FOOTSCORE_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGDATABASE", "footscore")
    for target in ("footscore", "footscore_restore_production", "postgres"):
        with pytest.raises(ValueError, match="disposable CI"):
            _guard(target)
    monkeypatch.setenv("PGHOST", "db.production.example")
    with pytest.raises(ValueError, match="disposable CI"):
        _guard("footscore_restore_ci")


def test_security_verifier_accepts_exact_disposable_ci(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FOOTSCORE_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGDATABASE", "footscore")
    assert _guard("footscore_restore_ci") == "footscore"
