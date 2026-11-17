"""Password change, all-device logout and bounded maintenance tests."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.maintenance_worker import run_cleanup
from app.models import AuthOneTimeToken, AuthRateBucket, User
from app.security import hash_password
from app.settings import settings


def _setup(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(settings, "jwt_secret", SecretStr("session-tests-" * 4))
    monkeypatch.setattr(settings, "require_verified_email", False)
    app.dependency_overrides[get_db] = override_db
    return engine


def test_logout_all_devices_requires_password_and_revokes_old_jwt(monkeypatch):
    engine = _setup(monkeypatch)
    try:
        with TestClient(app) as client:
            reg = client.post("/api/v1/auth/register", json={
                "email": "all-devices@example.com", "password": "StartingPass123!",
            })
            assert reg.status_code == 201
            login = client.post("/api/v1/auth/login", json={
                "email": "all-devices@example.com", "password": "StartingPass123!",
            })
            assert login.status_code == 200
            auth = {"Authorization": "Bearer " + login.json()["access_token"]}
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 200
            bad = client.post(
                "/api/v1/auth/sessions/revoke-all", headers=auth,
                json={"current_password": "WrongPassword123!"},
            )
            assert bad.status_code == 401
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 200
            logout = client.post(
                "/api/v1/auth/sessions/revoke-all", headers=auth,
                json={"current_password": "StartingPass123!"},
            )
            assert logout.status_code == 200
            assert logout.json() == {"sessions_revoked": True}
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
            assert client.post(
                "/api/v1/auth/sessions/revoke-all", headers=auth,
                json={"current_password": "StartingPass123!"},
            ).status_code == 401
            new_login = client.post("/api/v1/auth/login", json={
                "email": "all-devices@example.com", "password": "StartingPass123!",
            })
            assert new_login.status_code == 200
            assert new_login.json()["access_token"] != login.json()["access_token"]
            new_auth = {
                "Authorization": "Bearer " + new_login.json()["access_token"],
            }
            assert client.get("/api/v1/auth/me", headers=new_auth).status_code == 200
        with Session(engine) as db:
            assert db.scalar(select(User.session_version)) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_authenticated_password_change_revokes_prior_tokens(monkeypatch):
    engine = _setup(monkeypatch)
    try:
        with TestClient(app) as client:
            assert client.post("/api/v1/auth/register", json={
                "email": "change@example.com", "password": "OriginalPass123!",
            }).status_code == 201
            login = client.post("/api/v1/auth/login", json={
                "email": "change@example.com", "password": "OriginalPass123!",
            })
            auth = {"Authorization": "Bearer " + login.json()["access_token"]}
            bad = client.post("/api/v1/auth/password/change", headers=auth, json={
                "current_password": "WrongPassword123!",
                "new_password": "NewPassword123!",
            })
            assert bad.status_code == 401
            same = client.post("/api/v1/auth/password/change", headers=auth, json={
                "current_password": "OriginalPass123!",
                "new_password": "OriginalPass123!",
            })
            assert same.status_code == 422
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 200
            changed = client.post("/api/v1/auth/password/change", headers=auth, json={
                "current_password": "OriginalPass123!",
                "new_password": "NewPassword123!",
            })
            assert changed.status_code == 200
            assert changed.json() == {
                "password_changed": True, "sessions_revoked": True,
            }
            assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
            assert client.post("/api/v1/auth/login", json={
                "email": "change@example.com", "password": "OriginalPass123!",
            }).status_code == 401
            fresh = client.post("/api/v1/auth/login", json={
                "email": "change@example.com", "password": "NewPassword123!",
            })
            assert fresh.status_code == 200
            assert client.get("/api/v1/auth/me", headers={
                "Authorization": "Bearer " + fresh.json()["access_token"],
            }).status_code == 200
        with Session(engine) as db:
            assert db.scalar(select(User.session_version)) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_optional_maintenance_worker_respects_limits_and_dry_run(monkeypatch):
    engine = _setup(monkeypatch)
    now = datetime.now(timezone.utc)
    try:
        with Session(engine) as db:
            db.add(User(
                email="cleanup@example.com", password_hash=hash_password("SomePass123!"),
            ))
            db.flush()
            user_id = db.scalar(select(User.id))
            db.add_all([
                AuthRateBucket(
                    key=f"expired-bucket-{i}", used=1,
                    expires_at=now - timedelta(days=1),
                ) for i in range(3)
            ])
            db.add(AuthRateBucket(
                key="active-bucket", used=1, expires_at=now + timedelta(hours=3),
            ))
            db.add_all([
                AuthOneTimeToken(
                    user_id=user_id, purpose="verify_email",
                    token_hash=(str(i) * 64),
                    created_at=now - timedelta(days=3),
                    expires_at=now - timedelta(days=2),
                ) for i in range(2)
            ])
            db.add(AuthOneTimeToken(
                user_id=user_id, purpose="reset_password",
                token_hash="f" * 64,
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            ))
            db.commit()
        monkeypatch.setattr(
            "app.maintenance_worker.SessionLocal", lambda: Session(engine),
        )
        dry = run_cleanup(execute=False, max_batches=5, batch_size=2)
        assert dry["dry_run"] is True
        assert dry["batches"] == 1
        assert dry["expired_buckets_selected"] == 2
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(AuthRateBucket)) == 4
            assert db.scalar(select(func.count()).select_from(AuthOneTimeToken)) == 3
        partial = run_cleanup(execute=True, max_batches=2, batch_size=2)
        assert partial["batches"] == 2
        assert (partial["expired_buckets_selected"]
                + partial["expired_tokens_selected"]) == 4
        remaining = run_cleanup(execute=True, max_batches=5, batch_size=2)
        assert remaining["dry_run"] is False
        with Session(engine) as db:
            assert set(db.scalars(select(AuthRateBucket.key))) == {"active-bucket"}
            tokens = db.scalars(select(AuthOneTimeToken)).all()
            assert len(tokens) == 1
            assert tokens[0].token_hash == "f" * 64
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
