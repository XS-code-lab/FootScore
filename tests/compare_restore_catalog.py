"""CI-only PostgreSQL index, constraint, and sequence restore comparison.

Reads catalog metadata and exact sequence state for the same quiescent,
synthetic PostgreSQL source and its restored target. This does NOT run
against a live changing production database or print schema SQL.
"""
import json
import os

import psycopg
from psycopg import sql

def _connection(dbname: str):
    """Open one bounded, CI-only PostgreSQL connection."""
    params = {
        "host": os.environ["PGHOST"],
        "user": os.environ["PGUSER"],
        "dbname": dbname,
        "connect_timeout": 5,
    }
    if os.environ.get("PGPORT"):
        params["port"] = os.environ["PGPORT"]
    return psycopg.connect(**params)

INDEX_QUERY = """
    SELECT tablename, indexname, indexdef
    FROM pg_indexes WHERE schemaname = 'public'
    ORDER BY tablename, indexname
"""
CONSTRAINT_QUERY = """
    SELECT rel.relname, c.conname,
           c.contype, c.convalidated,
           pg_get_constraintdef(c.oid, true)
    FROM pg_constraint AS c
    JOIN pg_class AS rel ON rel.oid = c.conrelid
    JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
    WHERE ns.nspname = 'public'
    ORDER BY rel.relname, c.conname
"""
SEQUENCE_QUERY = """
    SELECT sequencename, start_value, min_value, max_value,
           increment_by, cycle, cache_size
    FROM pg_sequences WHERE schemaname = 'public'
    ORDER BY sequencename
"""


def catalog_snapshot(dbname: str) -> dict:
    """Read catalog objects and sequence last_value/is_called, without mutation."""
    with _connection(dbname) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with conn.cursor() as cursor:
            cursor.execute(INDEX_QUERY)
            indexes = {
                f"{table}.{name}": definition
                for table, name, definition in cursor.fetchall()
            }
            cursor.execute(CONSTRAINT_QUERY)
            constraints = {
                f"{table}.{name}": (kind, valid, definition)
                for table, name, kind, valid, definition in cursor.fetchall()
            }
            cursor.execute(SEQUENCE_QUERY)
            sequences = {}
            for row in cursor.fetchall():
                name, *attributes = row
                cursor.execute(
                    sql.SQL("SELECT last_value, is_called FROM {}").format(
                        sql.Identifier("public", name)
                    )
                )
                state = cursor.fetchone()
                sequences[name] = (tuple(attributes), tuple(state))
    if not indexes or not constraints or not sequences:
        raise RuntimeError("Incomplete PostgreSQL catalog snapshot")
    return {
        "indexes": indexes, "constraints": constraints, "sequences": sequences,
    }


def summarize_catalogs(source: dict, restored: dict) -> dict:
    """Only emit counts and object names, not SQL definitions or secret values."""
    mismatches = {}
    for kind in ("indexes", "constraints", "sequences"):
        before = source[kind]
        after = restored[kind]
        keys = sorted(set(before) | set(after))
        mismatches[kind] = [
            key for key in keys if before.get(key) != after.get(key)
        ]
    return {
        "catalogs_match": all(not value for value in mismatches.values()),
        "index_count": len(source["indexes"]),
        "constraint_count": len(source["constraints"]),
        "sequence_count": len(source["sequences"]),
        "mismatched_objects": mismatches,
        "sequence_values_exported": False,
        "index_definitions_exported": False,
        "production_recovery_verified": False,
    }


def main() -> None:
    import argparse
    from app.restore_audit import validate_drill_target

    parser = argparse.ArgumentParser(description="CI-only restored PostgreSQL catalog audit")
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    if (
        os.environ.get("FOOTSCORE_INTEGRATION_TESTS") != "1"
        or os.environ.get("GITHUB_ACTIONS") != "true"
    ):
        parser.error("Catalog comparison requires disposable GitHub Actions CI")
    source = os.environ.get("PGDATABASE", "")
    if not source or source.startswith("footscore_restore_"):
        parser.error("Source must be a disposable, non-restore CI database")
    try:
        validate_drill_target(args.target, source)
    except ValueError:
        parser.error("Invalid isolated restore database name")

    try:
        source_catalog = catalog_snapshot(source)
        restored_catalog = catalog_snapshot(args.target)
        result = summarize_catalogs(source_catalog, restored_catalog)
        if not result["catalogs_match"] and os.environ.get("FOOTSCORE_DEBUG_CONSTRAINTS") == "1":
            # Temporary diagnostic for four publicly versioned DDL checks
            # only. No row data, tokens, credentials or unlisted SQL output.
            import sys
            checks = {
                "auth_one_time_tokens.auth_one_time_tokens_purpose_check",
                "backup_alert_states.backup_alert_states_condition_check",
                "email_deliveries.email_deliveries_status_check",
                "historical_player_fetches.historical_player_fetches_status_check",
            }
            for name in result["mismatched_objects"]["constraints"]:
                if name in checks:
                    print(
                        "CI DDL CHECK", name,
                        "source:", source_catalog["constraints"].get(name),
                        "restored:", restored_catalog["constraints"].get(name),
                        file=sys.stderr,
                    )
    except (psycopg.Error, ValueError, RuntimeError) as exc:
        # Only exception *class* and PostgreSQL SQLSTATE are emitted: no
        # SQL queries, table contents, connection URLs or private values.
        state = getattr(exc, "sqlstate", None)
        kind = type(exc).__name__
        parser.exit(
            1, f"Could not verify PostgreSQL restored catalog ({kind}, "
            f"SQLSTATE={state or 'none'})\\n",
        )
    print(json.dumps(result, indent=2))
    if not result["catalogs_match"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
