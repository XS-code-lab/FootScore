"""CI-ONLY read-only, full-row source/restore PostgreSQL content comparison.

Reads every public base table in deterministic primary-key order using
bounded PostgreSQL server-side cursors. SHA-256 digests exist only in
process memory and are never printed, saved, or uploaded. Never use a
changing production source as evidence of an exact pg_dump snapshot:
this CI comparison requires an otherwise quiescent disposable database.
"""
import hashlib
import json
import os
from collections.abc import Iterable

import psycopg
from psycopg import sql

TABLE_QUERY = """
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name
"""
PK_QUERY = """
    SELECT kcu.column_name
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_catalog = kcu.constraint_catalog
     AND tc.constraint_schema = kcu.constraint_schema
     AND tc.constraint_name = kcu.constraint_name
     AND tc.table_name = kcu.table_name
    WHERE tc.table_schema = 'public' AND tc.table_name = %s
      AND tc.constraint_type = 'PRIMARY KEY'
    ORDER BY kcu.ordinal_position
"""


def digest_rows(rows: Iterable[str]) -> tuple[int, str]:
    """Frame row bytes so text concatenation cannot mask row-boundary changes."""
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        if not isinstance(row, str):
            raise TypeError("Canonical PostgreSQL row must be text")
        data = row.encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        count += 1
    return count, digest.hexdigest()


def summarize_comparison(source: dict, restored: dict) -> dict:
    """Never expose hashes or source row contents, including in error messages."""
    names = sorted(set(source) | set(restored))
    different = [name for name in names if source.get(name) != restored.get(name)]
    return {
        "all_tables_match": not different,
        "compared_tables": len(names),
        "compared_source_rows": sum(item[0] for item in source.values()),
        "compared_restored_rows": sum(item[0] for item in restored.values()),
        "mismatched_tables": different,
        "raw_rows_or_hashes_exported": False,
        "production_point_in_time_recovery_verified": False,
    }


def _connection(dbname: str):
    params = {
        "host": os.environ["PGHOST"],
        "user": os.environ["PGUSER"],
        "dbname": dbname,
        "connect_timeout": 5,
    }
    if os.environ.get("PGPORT"):
        params["port"] = os.environ["PGPORT"]
    return psycopg.connect(**params)


def manifest(dbname: str) -> dict[str, tuple[int, str]]:
    """Compute complete, deterministic per-table hashes without buffering rows."""
    result = {}
    with _connection(dbname) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        with conn.cursor() as metadata:
            metadata.execute(TABLE_QUERY)
            names = [row[0] for row in metadata.fetchall()]
            if not names or len(names) > 128:
                raise RuntimeError("Unexpected number of application tables")
            for table in names:
                metadata.execute(PK_QUERY, (table,))
                columns = [row[0] for row in metadata.fetchall()]
                if not columns:
                    # Refuse to produce false confidence for unordered tables.
                    raise RuntimeError("Backup comparison requires primary keys")
                statement = sql.SQL(
                    "SELECT row_to_json(t)::text FROM {} AS t ORDER BY {}"
                ).format(
                    sql.Identifier("public", table),
                    sql.SQL(", ").join(sql.Identifier("t", col) for col in columns),
                )
                with conn.cursor(name="backup_integrity_scan") as scan:
                    scan.execute(statement)
                    def read_rows():
                        while True:
                            batch = scan.fetchmany(256)
                            if not batch:
                                return
                            for row in batch:
                                yield row[0]
                    result[table] = digest_rows(read_rows())
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="CI-only PostgreSQL restore content audit")
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    if (
        os.getenv("FOOTSCORE_INTEGRATION_TESTS") != "1"
        or os.getenv("GITHUB_ACTIONS") != "true"
    ):
        parser.error("Only disposable GitHub Actions PostgreSQL integration is supported")
    source = os.environ.get("PGDATABASE", "")
    from app.restore_audit import validate_drill_target

    try:
        validate_drill_target(args.target, source)
    except ValueError:
        parser.error("Restore comparison requires a safe disposable target")
    if not source or source.startswith("footscore_restore_"):
        parser.error("Source must be a non-disposable CI test database")
    try:
        result = summarize_comparison(manifest(source), manifest(args.target))
    except Exception:
        # No SQL exception text; it can contain fragments of sensitive rows.
        parser.exit(1, "Restore content comparison could not complete\n")
    print(json.dumps(result, indent=2))
    if not result["all_tables_match"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
