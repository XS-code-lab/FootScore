"""Revoke all JWT sessions with a compare-and-swap version bump.

JWTs are stateless; the user session_version claim is compared on every
authenticated request. Mutations are performed atomically in PostgreSQL to
prevent concurrent old sessions from overwriting newer credentials.
"""
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import User
from app.security import hash_password, verify_password


def _revoke(
    db: Session, user: User, *, expected_version: int,
    password_hash: str | None = None,
) -> None:
    """Advance the session version if the credential snapshot is still current."""
    statement = (
        update(User)
        .where(
            User.id == user.id,
            User.is_active.is_(True),
            User.session_version == expected_version,
        )
        .values(session_version=User.session_version + 1)
    )
    if password_hash is not None:
        statement = statement.values(password_hash=password_hash)
    if db.execute(statement).rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=401, detail="Session changed; please sign in again",
        )
    db.commit()


def revoke_all_sessions(
    db: Session, user: User, current_password: str, *, expected_version: int,
) -> None:
    """Require reauthentication before logging out every existing device."""
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid current password")
    _revoke(db, user, expected_version=expected_version)


def change_password(
    db: Session, user: User, current_password: str, new_password: str,
    *, expected_version: int,
) -> None:
    """Change password and atomically revoke all existing JWT access tokens."""
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid current password")
    if new_password == current_password:
        raise HTTPException(
            status_code=422, detail="New password must differ from current password",
        )
    if not 12 <= len(new_password) <= 128:
        raise HTTPException(status_code=422, detail="Invalid new password length")
    _revoke(
        db, user, expected_version=expected_version,
        password_hash=hash_password(new_password),
    )
