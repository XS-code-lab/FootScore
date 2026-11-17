"""Auth abuse throttle tests: fixed window, IP pseudonymization and no bypass."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.db import Base, get_db
from app.main import app
from app.models import AuthRateBucket
from app.services.auth_rate import check_auth_limit
from app.settings import settings


def _request(peer: str = "198.51.100.15", spoof: str | None = None) -> Request:
    headers = []
    if spoof is not None:
        headers = [(b"x-forwarded-for", spoof.encode())]
    return Request({
        "type": "http", "method": "POST", "path": "/api/v1/auth/login",
        "client": (peer, 3456),
        "headers": headers, "query_string": b"", "server": ("localhost", 80),
        "scheme": "http",
    })


def test_login_bucket_is_hmac_pseudonymous_and_cannot_spoof_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "jwt_secret", SecretStr("r" * 48))
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    at = datetime(2026, 11, 17, 12, 4, tzinfo=timezone.utc)
    with Session(engine) as db:
        for attempt in range(10):
            assert check_auth_limit(
                db, _request(spoof=f"fake-{attempt}"), "login", now=at
            ) == attempt + 1
        with pytest.raises(HTTPException) as exc:
            check_auth_limit(db, _request(spoof="another"), "login", now=at)
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"].isdigit()
        assert 0 < int(exc.value.headers["Retry-After"]) <= 900
        keys = db.scalars(select(AuthRateBucket.key)).all()
        assert len(keys) == 1
        assert "198.51.100.15" not in keys[0]
        assert "fake-" not in keys[0]
        assert check_auth_limit(
            db, _request("198.51.100.16"), "login", now=at
        ) == 1
        assert check_auth_limit(
            db, _request(), "login", now=at + timedelta(minutes=15)
        ) == 1
        assert check_auth_limit(db, _request(), "register", now=at) == 1


def test_registration_limit_returns_429_before_creating_sixth_account(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "jwt_secret", SecretStr("s" * 48))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            for n in range(5):
                response = client.post("/api/v1/auth/register", json={
                    "email": f"pilot-{n}@example.com", "password": "SafePass12345!",
                })
                assert response.status_code == 201
            response = client.post("/api/v1/auth/register", json={
                "email": "sixth@example.com", "password": "SafePass12345!",
            })
            assert response.status_code == 429
            assert response.headers.get("Retry-After")
        with Session(engine) as db:
            from app.models import User

            assert db.scalar(select(func.count()).select_from(User)) == 5
            assert db.scalar(select(func.count()).select_from(
                AuthRateBucket
            )) == 1
    finally:
        app.dependency_overrides.clear()
