"""CI-only FastAPI smoke and sequence write check on RESTORED disposable DB.

Never run against a production or source database. Uses a separate process
with DATABASE_URL explicitly pointing at footscore_restore_ci, and makes
only in-process TestClient HTTP requests (no provider, SMTP or cloud calls).
"""
import os

from sqlalchemy.engine import make_url


def _require_isolated_ci() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("FOOTSCORE_INTEGRATION_TESTS") != "1"
        or os.environ.get("PGDATABASE") != "footscore"
        or os.environ.get("PGHOST") != "127.0.0.1"
    ):
        raise RuntimeError("Restored API smoke is permitted only in disposable CI")
    url = make_url(os.environ.get("DATABASE_URL", "sqlite:///"))
    if url.database != "footscore_restore_ci" or url.host != "127.0.0.1":
        raise RuntimeError("API must point to the isolated restore CI database")


def main() -> None:
    _require_isolated_ci()
    # Do not import the app before the database URL safety gate above.
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.main import app
    from app.models import LiveUpdate, Match

    with SessionLocal() as db:
        fixture = db.scalar(select(Match).where(
            Match.provider == "ci-synthetic",
            Match.provider_id == "ci-backup-drill-synthetic-v1",
        ))
        if fixture is None:
            raise AssertionError("Restored synthetic match missing")
        match_id = fixture.id
        original_high_match = db.scalar(select(func.max(Match.id))) or 0
        original_high_update = db.scalar(select(func.max(LiveUpdate.id))) or 0

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["database"] == "connected"

        matches = client.get("/api/v1/matches", params={
            "status": "FT", "limit": 100,
        })
        assert matches.status_code == 200
        assert any(
            row["id"] == match_id and row["provider_id"] ==
            "ci-backup-drill-synthetic-v1"
            for row in matches.json()
        )

        detail = client.get(f"/api/v1/matches/{match_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "FT"
        assert detail.json()["home_score"] == 2
        assert detail.json()["away_score"] == 1

        replay = client.get(f"/api/v1/matches/{match_id}/updates")
        assert replay.status_code == 200
        assert len(replay.json()) == 1
        assert replay.json()[0]["kind"] == "ci_restore_sentinel"
        assert replay.json()[0]["data"] == {
            "value": 42, "synthetic_only": True,
        }
        last_id = replay.json()[0]["id"]
        assert client.get(
            f"/api/v1/matches/{match_id}/updates",
            params={"after_id": last_id},
        ).json() == []
        assert client.get(f"/api/v1/matches/{match_id}/events").json() == []

        # This is the *only* mutating step, and happens strictly in the
        # isolated restored test database AFTER source/restore comparisons.
        with SessionLocal.begin() as db:
            inserted = Match(
                provider="ci-synthetic",
                provider_id="ci-post-restore-sequence-smoke",
                status="FT", home_score=0, away_score=0,
            )
            db.add(inserted)
            db.flush()
            assert inserted.id > original_high_match
            update = LiveUpdate(
                match_id=inserted.id, kind="ci_post_restore_write",
                payload={"restored_only": True},
            )
            db.add(update)
            db.flush()
            assert update.id > original_high_update
            inserted_id = inserted.id
            inserted_update_id = update.id

        after = client.get(f"/api/v1/matches/{inserted_id}")
        assert after.status_code == 200
        assert after.json()["provider_id"] == "ci-post-restore-sequence-smoke"
        events = client.get(f"/api/v1/matches/{inserted_id}/updates").json()
        assert len(events) == 1
        assert events[0]["id"] == inserted_update_id
        assert events[0]["data"] == {"restored_only": True}

    print(
        "Restored FastAPI match list/detail/replay and isolated "
        "post-restore match/update sequence writes passed."
    )


if __name__ == "__main__":
    main()
