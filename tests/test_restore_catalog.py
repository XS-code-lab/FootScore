"""Restored PostgreSQL catalogs must match beyond table row contents."""
from copy import deepcopy

from tests.compare_restore_catalog import summarize_catalogs


def baseline() -> dict:
    return {
        "indexes": {
            "matches.matches_pkey": "CREATE UNIQUE INDEX matches_pkey ON matches USING btree (id)",
            "matches.ix_matches_provider": "CREATE INDEX ix_matches_provider ON matches (provider)",
        },
        "constraints": {
            "matches.matches_pkey": ("p", True, "PRIMARY KEY (id)"),
            "users.users_email_key": ("u", True, "UNIQUE (email)"),
        },
        "sequences": {
            "matches_id_seq": ((1, 1, 9223372036854775807, 1, False, 1), (181, True)),
            "users_id_seq": ((1, 1, 9223372036854775807, 1, False, 1), (31, True)),
        },
    }


def test_equal_restored_indexes_constraints_and_sequences() -> None:
    original = baseline()
    result = summarize_catalogs(original, deepcopy(original))
    assert result["catalogs_match"]
    assert result["index_count"] == 2
    assert result["constraint_count"] == 2
    assert result["sequence_count"] == 2
    assert result["mismatched_objects"] == {
        "indexes": [], "constraints": [], "sequences": [],
    }
    assert result["sequence_values_exported"] is False
    assert result["index_definitions_exported"] is False
    assert result["production_recovery_verified"] is False


def test_modified_index_is_detected_without_sql_leakage() -> None:
    original = baseline()
    changed = deepcopy(original)
    changed["indexes"]["matches.ix_matches_provider"] = "SECRET CHANGED DEFINITION"
    result = summarize_catalogs(original, changed)
    assert result["catalogs_match"] is False
    assert result["mismatched_objects"]["indexes"] == ["matches.ix_matches_provider"]
    assert "SECRET CHANGED DEFINITION" not in str(result)


def test_sequence_value_drift_is_detected() -> None:
    original = baseline()
    changed = deepcopy(original)
    changed["sequences"]["matches_id_seq"] = (
        changed["sequences"]["matches_id_seq"][0], (182, True),
    )
    result = summarize_catalogs(original, changed)
    assert result["catalogs_match"] is False
    assert result["mismatched_objects"]["sequences"] == ["matches_id_seq"]
    assert "(182, True)" not in str(result)


def test_sequence_is_called_drift_is_detected() -> None:
    original = baseline()
    changed = deepcopy(original)
    changed["sequences"]["matches_id_seq"] = (
        changed["sequences"]["matches_id_seq"][0], (181, False),
    )
    assert not summarize_catalogs(original, changed)["catalogs_match"]


def test_missing_or_added_constraints_fail() -> None:
    original = baseline()
    changed = deepcopy(original)
    del changed["constraints"]["users.users_email_key"]
    changed["constraints"]["new_check"] = ("c", True, "CHECK (1=1)")
    result = summarize_catalogs(original, changed)
    assert result["mismatched_objects"]["constraints"] == [
        "new_check", "users.users_email_key",
    ]
    assert result["catalogs_match"] is False
