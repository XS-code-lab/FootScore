"""CI-only least-privilege smoke using a temporary NOLOGIN PostgreSQL role.

All role creation, GRANTs and denied DML attempts are restricted to the
isolated restored database in a disposable GitHub Actions container.
Tests are NEVER run on the source or on real hosted PostgreSQL.
"""
import os

from sqlalchemy.engine import make_url

ROLE = "footscore_ci_restore_reader"


def require_disposable_restored_database() -> None:
    url = make_url(os.environ.get("DATABASE_URL", "sqlite:///"))
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("FOOTSCORE_INTEGRATION_TESTS") != "1"
        or os.environ.get("PGHOST") != "127.0.0.1"
        or os.environ.get("PGDATABASE") != "footscore"
        or os.environ.get("PGUSER") != "footscore"
        or url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.database != "footscore_restore_ci"
        or url.username != "footscore"
    ):
        raise RuntimeError("Read-only role smoke requires exact disposable restore CI")


def smoke() -> None:
    require_disposable_restored_database()
    import psycopg

    params = {
        "host": "127.0.0.1", "user": "footscore",
        "dbname": "footscore_restore_ci",
        "port": os.environ.get("PGPORT", "5432"),
        "connect_timeout": 5, "autocommit": True,
    }
    with psycopg.connect(**params) as conn:
        with conn.cursor() as admin:
            admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
            if admin.fetchone() is not None:
                raise RuntimeError("Temporary CI reader role already exists")
            admin.execute(
                f"CREATE ROLE {ROLE} NOLOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT"
            )
            try:
                admin.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
                admin.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}")
                admin.execute(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin "
                    "FROM pg_roles WHERE rolname = %s",
                    (ROLE,),
                )
                assert admin.fetchone() == (False, False, False, False)
                admin.execute("BEGIN")
                try:
                    admin.execute(f"SET LOCAL ROLE {ROLE}")
                    admin.execute("SELECT current_user")
                    assert admin.fetchone()[0] == ROLE
                    admin.execute("SELECT count(*) FROM public.matches")
                    assert admin.fetchone()[0] >= 181
                    admin.execute("SELECT count(*) FROM public.live_updates")
                    assert admin.fetchone()[0] >= 1441
                    for statement in (
                        "INSERT INTO public.matches DEFAULT VALUES",
                        "UPDATE public.matches SET status = status WHERE FALSE",
                        "DELETE FROM public.matches WHERE FALSE",
                        "CREATE TABLE public.ci_forbidden_write(id int)",
                    ):
                        admin.execute("SAVEPOINT ci_denied_operation")
                        try:
                            admin.execute(statement)
                        except psycopg.errors.InsufficientPrivilege:
                            admin.execute("ROLLBACK TO SAVEPOINT ci_denied_operation")
                        else:
                            admin.execute("ROLLBACK TO SAVEPOINT ci_denied_operation")
                            raise AssertionError("Read-only role unexpectedly permitted write")
                finally:
                    admin.execute("ROLLBACK")
            finally:
                # Grant removal and role deletion happen only on the
                # disposable target, even when an assertion fails.
                admin.execute(f"DROP OWNED BY {ROLE}")
                admin.execute(f"DROP ROLE {ROLE}")
    print(
        "Disposable NOLOGIN reader role can SELECT restored football data "
        "and cannot INSERT/UPDATE/DELETE/CREATE; test role removed."
    )


if __name__ == "__main__":
    smoke()
