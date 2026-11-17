"""Read-only content verification detects drift even with equal row counts."""
from tests.compare_restore_content import digest_rows, summarize_comparison


def test_identical_content_and_table_order_are_stable():
    first = digest_rows(['{"id":1,"value":"a"}', '{"id":2,"value":"b"}'])
    second = digest_rows(iter(['{"id":1,"value":"a"}', '{"id":2,"value":"b"}']))
    report = summarize_comparison({"matches": first}, {"matches": second})
    assert report["all_tables_match"] is True
    assert report["compared_tables"] == 1
    assert report["compared_source_rows"] == 2
    assert report["compared_restored_rows"] == 2
    assert not report["raw_rows_or_hashes_exported"]
    assert not report["production_point_in_time_recovery_verified"]


def test_equal_row_count_but_changed_payload_is_rejected():
    original = digest_rows(['{"id":1,"score":2}', '{"id":2,"score":0}'])
    altered = digest_rows(['{"id":1,"score":3}', '{"id":2,"score":0}'])
    assert original[0] == altered[0] == 2
    report = summarize_comparison(
        {"live_updates": original}, {"live_updates": altered},
    )
    assert report["all_tables_match"] is False
    assert report["mismatched_tables"] == ["live_updates"]
    assert original[1] not in str(report)
    assert altered[1] not in str(report)


def test_missing_or_extra_table_is_detected():
    match = digest_rows(['{"id":1}'])
    report = summarize_comparison(
        {"matches": match, "users": digest_rows([])},
        {"matches": match, "unexpected": digest_rows([])},
    )
    assert report["all_tables_match"] is False
    assert report["mismatched_tables"] == ["unexpected", "users"]


def test_row_boundaries_and_order_are_in_hash():
    one_row = digest_rows(["ab", "c"])
    other = digest_rows(["a", "bc"])
    reversed_rows = digest_rows(["c", "ab"])
    assert one_row[0] == other[0] == reversed_rows[0] == 2
    assert len({one_row[1], other[1], reversed_rows[1]}) == 3


def test_empty_table_still_has_a_digest_and_zero_count():
    empty = digest_rows([])
    assert empty[0] == 0
    assert len(empty[1]) == 64
