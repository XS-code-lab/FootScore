"""Account lifecycle tests: no raw codes at rest, replay rejection, JWT revocation."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import AuthOneTimeToken, AuthRateBucket, User
from app.services import account_recovery
from app.services.maintenance import cleanup_auth_state
from app.settings import settings


def _setup(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(settings, "jwt_secret", SecretStr("recovery-test-" * 4))
    monkeypatch.setattr(settings, "require_verified_email", True)
    messages = []
    monkeypatch.setattr(account_recovery, "email_is_configured", lambda: True)
    monkeypatch.setattr(
        account_recovery, "send_email_code",
        lambda email, token, purpose: messages.append((email, token, purpose)),
    )
    return engine, messages


def test_verification_password_reset_one_time_and_jwt_revocation(monkeypatch):
    engine, sent = _setup(monkeypatch)
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/register", json={
                "email": "Reviewer@Example.com", "password": "OriginalStrong123!",
            })
            assert response.status_code == 201
            assert len(sent) == 1
            address, verification_code, purpose = sent.pop()
            assert (address, purpose) == ("reviewer@example.com", "verify_email")
            with Session(engine) as db:
                token_row = db.scalar(select(AuthOneTimeToken))
                assert token_row.token_hash != verification_code
                assert verification_code not in str(token_row.token_hash)
                assert db.scalar(select(func.count()).select_from(
                    AuthOneTimeToken
                )) == 1

            denied = client.post("/api/v1/auth/login", json={
                "email": address, "password": "OriginalStrong123!",
            })
            assert denied.status_code == 403
            assert client.post("/api/v1/auth/password-reset/confirm", json={
                "token": verification_code,
                "new_password": "AnotherStrong123!",
            }).status_code == 400
            ok = client.post("/api/v1/auth/verification/confirm", json={
                "token": verification_code,
            })
            assert ok.status_code == 200
            assert ok.json() == {"email_verified": True}
            assert client.post("/api/v1/auth/verification/confirm", json={
                "token": verification_code,
            }).status_code == 400

            login = client.post("/api/v1/auth/login", json={
                "email": address, "password": "OriginalStrong123!",
            })
            assert login.status_code == 200
            old_token = login.json()["access_token"]
            headers = {"Authorization": "Bearer " + old_token}
            assert client.get("/api/v1/auth/me", headers=headers).json() == {
                "id": response.json()["id"], "email": address, "email_verified": True,
            }

            # The same public response must cover unknown and known emails.
            unknown = client.post("/api/v1/auth/password-reset/request", json={
                "email": "missing@example.com",
            })
            known = client.post("/api/v1/auth/password-reset/request", json={
                "email": address,
            })
            assert unknown.status_code == known.status_code == 202
            assert unknown.json() == known.json()
            assert len(sent) == 1
            _, reset_code, reset_purpose = sent.pop()
            assert reset_purpose == "reset_password"
            assert reset_code != verification_code

            changed = client.post("/api/v1/auth/password-reset/confirm", json={
                "token": reset_code,
                "new_password": "UpdatedStrong123!",
            })
            assert changed.status_code == 200
            assert changed.json() == {"password_reset": True}
            assert client.post("/api/v1/auth/password-reset/confirm", json={
                "token": reset_code,
                "new_password": "OtherStrong123!",
            }).status_code == 400

            assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
            assert client.post("/api/v1/auth/login", json={
                "email": address, "password": "OriginalStrong123!",
            }).status_code == 401
            new_login = client.post("/api/v1/auth/login", json={
                "email": address, "password": "UpdatedStrong123!",
            })
            assert new_login.status_code == 200
            assert new_login.json()["access_token"] != old_token

        with Session(engine) as db:
            user = db.scalar(select(User))
            assert user.email_verified_at is not None
            assert user.session_version == 1
            assert db.scalar(select(func.count()).select_from(
                AuthOneTimeToken
            ).where(AuthOneTimeToken.consumed_at.is_(None))) == 0
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_unknown_request_expiration_and_cleanup(monkeypatch):
    engine, sent = _setup(monkeypatch)
    try:
        # No SMTP -> no token generation, and both known/unknown requests
        # return the same generic response even when email can't be sent.
        monkeypatch.setattr(account_recovery, "email_is_configured", lambda: False)
        with TestClient(app) as client:
            assert client.post("/api/v1/auth/register", json={
                "email": "test@example.com", "password": "OriginalStrong123!",
            }).status_code == 201
            response = client.post("/api/v1/auth/password-reset/request", json={
                "email": "test@example.com",
            })
            assert response.status_code == 202
            assert not sent
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(AuthOneTimeToken)) == 0

        monkeypatch.setattr(account_recovery, "email_is_configured", lambda: True)
        with TestClient(app) as client:
            assert client.post("/api/v1/auth/verification/request", json={
                "email": "test@example.com",
            }).status_code == 202
            assert len(sent) == 1
            _, token, purpose = sent.pop()
            assert purpose == "verify_email"
            with Session(engine) as db:
                rec = db.scalar(select(AuthOneTimeToken))
                rec.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
                db.add(AuthRateBucket(
                    key="expired-test",
                    used=1,
                    expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                ))
                db.add(AuthRateBucket(
                    key="active-test",
                    used=1,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ))
                db.commit()
            assert client.post("/api/v1/auth/verification/confirm", json={
                "token": token,
            }).status_code == 400

        with Session(engine) as db:
            dry = cleanup_auth_state(db)
            assert dry == {
                "dry_run": True, "expired_buckets_selected": 1,
                "expired_tokens_selected": 1, "max_rows": 1000,
            }
            assert db.scalar(select(func.count()).select_from(
                AuthOneTimeToken
            )) == 1
            result = cleanup_auth_state(db, execute=True)
            assert result["dry_run"] is False
            assert db.scalar(select(func.count()).select_from(AuthOneTimeToken)) == 0
            keys = set(db.scalars(select(AuthRateBucket.key)).all())
            assert "active-test" in keys
            assert "expired-test" not in keys
            assert len(keys) >= 2
            with pytest.raises(ValueError, match="Cleanup limit"):
                cleanup_auth_state(db, limit=0)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
