"""Password hashing and short-lived access tokens for FootScore clients."""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.settings import settings

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, stored: str) -> bool:
    return password_hash.verify(password, stored)


def _jwt_secret() -> str:
    secret = settings.jwt_secret.get_secret_value() if settings.jwt_secret else ""
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return secret


def issue_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "iss": "footscore",
        "aud": "footscore-api",
    }
    return jwt.encode(claims, _jwt_secret(), algorithm="HS256")


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    error = HTTPException(
        status_code=401,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise error
    try:
        claims = jwt.decode(
            credentials.credentials,
            _jwt_secret(),
            algorithms=["HS256"],
            audience="footscore-api",
            issuer="footscore",
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
        user_id = int(claims["sub"])
    except (InvalidTokenError, TypeError, ValueError, KeyError) as exc:
        raise error from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise error
    return user
