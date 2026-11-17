"""Restored PostgreSQL catalogs must match beyond table row contents."""
from copy import deepcopy

from tests.compare_restore_catalog import canonical_constraint, summarize_catalogs


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


def test_pg_dump_equivalent_varchar_array_cast_rewrite_is_recognized() -> None:
    name = "auth_one_time_tokens.auth_one_time_tokens_purpose_check"
    source = (
        "c", True,
        "CHECK (purpose::text = ANY (ARRAY['verify_email'::character varying, "
        "'reset_password'::character varying]::text[]))",
    )
    restored = (
        "c", True,
        "CHECK (purpose::text = ANY (ARRAY['verify_email'::character varying::text, "
        "'reset_password'::character varying::text]))",
    )
    assert canonical_constraint(name, source) == canonical_constraint(name, restored)
    left = baseline()
    left["constraints"][name] = source
    right = deepcopy(left)
    right["constraints"][name] = restored
    assert summarize_catalogs(left, right)["catalogs_match"]


def test_pg_dump_equivalence_does_not_hide_changed_enum_values_or_columns() -> None:
    name = "email_deliveries.email_deliveries_status_check"
    source = (
        "c", True,
        "CHECK (status::text = ANY (ARRAY['pending'::character varying, "
        "'sent'::character varying]::text[]))",
    )
    same = (
        "c", True,
        "CHECK (status::text = ANY (ARRAY['pending'::character varying::text, "
        "'sent'::character varying::text]))",
    )
    replaced_status = (
        "c", True,
        "CHECK (status::text = ANY (ARRAY['pending'::character varying::text, "
        "'failed'::character varying::text]))",
    )
    replaced_field = (
        "c", True,
        "CHECK (purpose::text = ANY (ARRAY['pending'::character varying::text, "
        "'sent'::character varying::text]))",
    )
    assert canonical_constraint(name, source) == canonical_constraint(name, same)
    assert canonical_constraint(name, source) != canonical_constraint(name, replaced_status)
    assert canonical_constraint(name, source) != canonical_constraint(name, replaced_field)
    assert canonical_constraint(name, source) != canonical_constraint(
        name, (source[0], False, same[2]),
    )


def test_cast_normalization_is_strictly_limited_to_known_constraints() -> None:
    source = (
        "c", True,
        "CHECK (status::text = ANY (ARRAY['pending'::character varying]::text[]))",
    )
    restored = (
        "c", True,
        "CHECK (status::text = ANY (ARRAY['pending'::character varying::text]))",
    )
    unknown = "custom_table.custom_check"
    assert canonical_constraint(unknown, source) == source
    assert canonical_constraint(unknown, restored) == restored
    assert canonical_constraint(unknown, source) != canonical_constraint(unknown, restored)
