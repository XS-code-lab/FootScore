"""Read-only PostgreSQL runtime-role privilege audit.

Use a distinct application DATABASE_URL and the known migration role name.
Never alters roles, grants, tables or database configuration. Reports only
booleans and counts, never usernames, passwords or private row values.
"""
import argparse
import json
import os

import psycopg

TABLE_QUERY = """
    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE pg_get_userbyid(relowner) = current_user),
           COUNT(*) FILTER (WHERE NOT (
               has_table_privilege(c.oid, 'SELECT') AND
               has_table_privilege(c.oid, 'INSERT') AND
               has_table_privilege(c.oid, 'UPDATE') AND
               has_table_privilege(c.oid, 'DELETE')
           )),
           COUNT(*) FILTER (WHERE
               has_table_privilege(c.oid, 'TRUNCATE') OR
               has_table_privilege(c.oid, 'REFERENCES') OR
               has_table_privilege(c.oid, 'TRIGGER')
           )
    FROM pg_class AS c
    JOIN pg_namespace AS ns ON ns.oid = c.relnamespace
    WHERE ns.nspname = 'public' AND c.relkind IN ('r', 'p')
"""
SEQUENCE_QUERY = """
    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE NOT has_sequence_privilege(c.oid, 'USAGE'))
    FROM pg_class AS c
    JOIN pg_namespace AS ns ON ns.oid = c.relnamespace
    WHERE ns.nspname = 'public' AND c.relkind = 'S'
"""


def summarize_runtime_permissions(raw: dict) -> dict:
    """A strict operator gate, not a global or production security audit."""
    prohibited = (
        "superuser", "createdb", "createrole", "replication",
        "bypassrls", "db_create", "schema_create",
        "migration_role_member",
    )
    needed = (
        "login", "schema_usage", "different_migration_role",
    )
    expected_counts = (
        "table_count", "table_owned", "table_missing_crud",
        "table_unsafe_privileges", "sequence_count", "sequence_missing_usage",
    )
    if any(name not in raw or type(raw[name]) is not bool for name in prohibited + needed):
        raise ValueError("Incomplete runtime privilege flag snapshot")
    if any(
        name not in raw or type(raw[name]) is not int or raw[name] < 0
        for name in expected_counts
    ):
        raise ValueError("Incomplete runtime privilege count snapshot")
    ok = (
        not any(raw[name] for name in prohibited)
        and all(raw[name] for name in needed)
        and raw["table_count"] > 0 and raw["sequence_count"] > 0
        and not any(raw[name] for name in (
            "table_owned", "table_missing_crud", "table_unsafe_privileges",
            "sequence_missing_usage",
        ))
    )
    return {
        "runtime_role_policy_passed": ok,
        "table_count": raw["table_count"],
        "sequence_count": raw["sequence_count"],
        "owned_tables": raw["table_owned"],
        "tables_missing_expected_crud": raw["table_missing_crud"],
        "tables_with_excessive_privileges": raw["table_unsafe_privileges"],
        "sequences_missing_usage": raw["sequence_missing_usage"],
        "can_login": raw["login"],
        "schema_usage_allowed": raw["schema_usage"],
        "schema_create_allowed": raw["schema_create"],
        "database_create_allowed": raw["db_create"],
        "is_superuser": raw["superuser"],
        "is_database_admin": raw["createdb"] or raw["createrole"],
        "is_migrator_member": raw["migration_role_member"],
        "migrator_is_distinct": raw["different_migration_role"],
        "role_names_or_credentials_exported": False,
        "cluster_global_roles_restored": False,
        "production_security_audit_completed": False,
    }


def audit_runtime_connection(conn, migration_role: str) -> dict:
    """Only catalog/privilege SELECTs; requires a known migrator role."""
    if not migration_role or migration_role.isspace():
        raise ValueError("Provide the migration owner role name")
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole, "
            "r.rolreplication, r.rolbypassrls, r.rolcanlogin, "
            "has_schema_privilege(current_user, 'public', 'USAGE'), "
            "has_schema_privilege(current_user, 'public', 'CREATE'), "
            "has_database_privilege(current_database(), 'CREATE'), "
            "current_user <> CAST(%s AS name), "
            "pg_has_role(current_user, CAST(%s AS name), 'MEMBER') "
            "FROM pg_roles AS r WHERE r.rolname = current_user",
            (migration_role, migration_role),
        )
        flags = cursor.fetchone()
        if flags is None:
            raise RuntimeError("Could not inspect database runtime role")
        cursor.execute(TABLE_QUERY)
        tables = cursor.fetchone()
        cursor.execute(SEQUENCE_QUERY)
        sequences = cursor.fetchone()
    names = (
        "superuser", "createdb", "createrole", "replication", "bypassrls",
        "login", "schema_usage", "schema_create", "db_create",
        "different_migration_role", "migration_role_member",
    )
    raw = dict(zip(names, flags, strict=True))
    raw.update(dict(zip(
        ("table_count", "table_owned", "table_missing_crud",
         "table_unsafe_privileges"), tables, strict=True,
    )))
    raw.update(dict(zip(
        ("sequence_count", "sequence_missing_usage"), sequences, strict=True,
    )))
    return summarize_runtime_permissions(raw)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL app runtime role audit"
    )
    parser.add_argument("--migration-role", required=True)
    parser.add_argument(
        "--strict", action="store_true", help="Exit 2 if the runtime policy fails",
    )
    args = parser.parse_args()
    raw = os.environ.get("DATABASE_URL", "")
    if not raw.startswith("postgresql+psycopg://"):
        parser.error("DATABASE_URL must use PostgreSQL psycopg")
    # Preserve URL query arguments (SSL/TLS verification, options, etc.).
    dsn = "postgresql://" + raw[len("postgresql+psycopg://"):]
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            report = audit_runtime_connection(conn, args.migration_role)
    except Exception:
        # Never echo connector errors, database credentials or role names.
        parser.exit(1, "Could not audit PostgreSQL runtime privileges\n")
    print(json.dumps(report, indent=2))
    if args.strict and not report["runtime_role_policy_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
