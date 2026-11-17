"""CI-only PostgreSQL restored object privileges and extension parity.

Compare role names, effective table/sequence ACLs, owner, RLS settings,
schema owners, and extension names/versions on a quiescent synthetic source
and its isolated restored database. Global roles, passwords, DB-wide
permissions and real production RBAC are intentionally OUT OF SCOPE.
"""
import argparse
import json
import os

import psycopg

from app.restore_audit import validate_drill_target

SECURITY_QUERY = """
    SELECT c.relname, c.relkind, pg_get_userbyid(c.relowner),
           c.relrowsecurity, c.relforcerowsecurity, c.relpersistence,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                ELSE pg_get_userbyid(a.grantee) END,
           pg_get_userbyid(a.grantor), a.privilege_type, a.is_grantable
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    LEFT JOIN LATERAL aclexplode(
        COALESCE(
            c.relacl,
            acldefault((CASE WHEN c.relkind = 'S' THEN 'S' ELSE 'r' END)::"char",
                       c.relowner)
        )
    ) AS a ON TRUE
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
    ORDER BY c.relname
"""
EXTENSION_QUERY = """
    SELECT e.extname, e.extversion, n.nspname
    FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    ORDER BY e.extname
"""
SCHEMA_QUERY = """
    SELECT n.nspname, pg_get_userbyid(n.nspowner),
           CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                ELSE pg_get_userbyid(a.grantee) END,
           pg_get_userbyid(a.grantor), a.privilege_type, a.is_grantable
    FROM pg_namespace AS n
    LEFT JOIN LATERAL aclexplode(
        COALESCE(n.nspacl, acldefault('n', n.nspowner))
    ) AS a ON TRUE
    WHERE n.nspname = 'public'
    ORDER BY n.nspname
"""


def _guard(target: str) -> str:
    source = os.environ.get("PGDATABASE", "")
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("FOOTSCORE_INTEGRATION_TESTS") != "1"
        or os.environ.get("PGHOST") != "127.0.0.1"
        or source != "footscore"
        or target != "footscore_restore_ci"
    ):
        raise ValueError("Database security parity is restricted to disposable CI")
    validate_drill_target(target, source)
    return source


def _connect(dbname: str):
    return psycopg.connect(
        host="127.0.0.1", user=os.environ["PGUSER"], dbname=dbname,
        port=os.environ.get("PGPORT", "5432"), connect_timeout=5,
    )


def snapshot(dbname: str) -> dict:
    """Read only, snapshot-consistent database-local security metadata."""
    with _connect(dbname) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with connection.cursor() as cursor:
            cursor.execute(SECURITY_QUERY)
            objects = {}
            for row in cursor.fetchall():
                name, kind, owner, rls, force, persistence, *acl = row
                key = f"{name}:{kind}"
                if key not in objects:
                    objects[key] = {
                        "owner": owner, "rls": rls, "force_rls": force,
                        "persistence": persistence, "acl": [],
                    }
                if acl[2] is not None:
                    objects[key]["acl"].append(tuple(acl))
            for item in objects.values():
                item["acl"] = tuple(sorted(item["acl"]))
            cursor.execute(EXTENSION_QUERY)
            extensions = {name: (version, schema) for name, version, schema
                          in cursor.fetchall()}
            cursor.execute(SCHEMA_QUERY)
            schemas = {}
            for name, owner, *acl in cursor.fetchall():
                if name not in schemas:
                    schemas[name] = {"owner": owner, "acl": []}
                if acl[2] is not None:
                    schemas[name]["acl"].append(tuple(acl))
            for item in schemas.values():
                item["acl"] = tuple(sorted(item["acl"]))
    if not objects or not extensions or not schemas:
        raise RuntimeError("Incomplete restored privilege metadata")
    return {"objects": objects, "extensions": extensions, "schemas": schemas}


def summarize(source: dict, restored: dict) -> dict:
    """Show ONLY object names and coverage; never emit role/ACL contents."""
    names = ("objects", "extensions", "schemas")
    mismatches = {}
    for kind in names:
        before, after = source[kind], restored[kind]
        mismatches[kind] = [
            name for name in sorted(set(before) | set(after))
            if before.get(name) != after.get(name)
        ]
    return {
        "security_metadata_matches": not any(mismatches.values()),
        "objects_checked": len(source["objects"]),
        "extensions_checked": len(source["extensions"]),
        "schemas_checked": len(source["schemas"]),
        "mismatched_objects": mismatches,
        "acl_details_exported": False,
        "role_secrets_exported": False,
        "global_role_recreation_verified": False,
        "production_least_privilege_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare isolated restored PostgreSQL ACL and extension metadata"
    )
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        source = _guard(args.target)
    except ValueError:
        parser.error("Only isolated disposable PostgreSQL CI target is permitted")
    try:
        result = summarize(snapshot(source), snapshot(args.target))
    except Exception as exc:
        # SQLSTATE and exception type are safe; never include SQL text,
        # role grants, credentials or connection URI in failure output.
        code = getattr(exc, "sqlstate", None)
        parser.exit(
            1, f"Could not verify restored security metadata "
            f"({type(exc).__name__}, SQLSTATE={code or 'none'})\\n",
        )
    print(json.dumps(result, indent=2))
    if not result["security_metadata_matches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
