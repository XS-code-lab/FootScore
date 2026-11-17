"""Authenticated user, player favorites and notification inbox API."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    FavoritePlayer, Notification, NotificationPreference, Player, PushDevice, User,
)
from app.security import current_user, hash_password, issue_token, verify_password
from app.services import account_recovery
from app.services.auth_rate import check_auth_limit
from app.settings import settings

router = APIRouter(prefix="/api/v1", tags=["accounts"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    password: str = Field(min_length=12, max_length=128)


class PreferencesUpdate(BaseModel):
    goals: bool | None = None
    assists: bool | None = None
    final_rating: bool | None = None
    min_final_rating: float | None = Field(default=None, ge=0, le=10)


class EmailRequest(BaseModel):
    email: EmailStr


class OneTimeCode(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class PasswordResetConfirm(OneTimeCode):
    new_password: str = Field(min_length=12, max_length=128)


class DeviceRegistration(BaseModel):
    token: str = Field(min_length=20, max_length=512)


def _email(value: str) -> str:
    return str(value).strip().lower()


def _pref(db: Session, user_id: int) -> NotificationPreference:
    pref = db.scalar(select(NotificationPreference).where(
        NotificationPreference.user_id == user_id,
    ))
    if pref is None:
        pref = NotificationPreference(user_id=user_id)
        db.add(pref)
        db.flush()
    return pref


def _pref_json(pref: NotificationPreference) -> dict:
    return {
        "goals": pref.goals,
        "assists": pref.assists,
        "final_rating": pref.final_rating,
        "min_final_rating": pref.min_final_rating,
    }


@router.post("/auth/register", status_code=201)
def register(
    data: Registration, request: Request, db: Session = Depends(get_db),
) -> dict:
    check_auth_limit(db, request, "register")
    email = _email(data.email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.flush()
        _pref(db, user.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from exc
    # Generate a code only if a secure SMTP sender has been configured.
    # Do not expose the one-time code in an HTTP response or in logs.
    if account_recovery.email_is_configured():
        with db.begin():
            pass
        account_recovery.request_code(db, user.email, "verify_email")
    return {"id": user.id, "email": user.email}


@router.post("/auth/login")
def login(
    data: Credentials, request: Request, db: Session = Depends(get_db),
) -> dict:
    check_auth_limit(db, request, "login")
    user = db.scalar(select(User).where(User.email == _email(data.email)))
    if user is None or not user.is_active or not verify_password(
        data.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if settings.require_verified_email and user.email_verified_at is None:
        raise HTTPException(status_code=403, detail="Email confirmation required")
    return {
        "access_token": issue_token(user.id, user.session_version),
        "token_type": "bearer",
    }


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict:
    return {
        "id": user.id, "email": user.email,
        "email_verified": user.email_verified_at is not None,
    }


@router.get("/favorites/players")
def list_favorites(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(FavoritePlayer, Player)
        .join(Player, Player.id == FavoritePlayer.player_id)
        .where(FavoritePlayer.user_id == user.id)
        .order_by(FavoritePlayer.created_at, FavoritePlayer.id)
    )
    return [
        {"player_id": player.id, "name": player.name, "created_at": fav.created_at}
        for fav, player in rows
    ]


@router.put("/favorites/players/{player_id}")
def add_favorite(
    player_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    fav = db.scalar(select(FavoritePlayer).where(
        FavoritePlayer.user_id == user.id,
        FavoritePlayer.player_id == player_id,
    ))
    if fav is None:
        db.add(FavoritePlayer(user_id=user.id, player_id=player_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"player_id": player_id, "favorite": True}


@router.delete("/favorites/players/{player_id}", status_code=204)
def remove_favorite(
    player_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    fav = db.scalar(select(FavoritePlayer).where(
        FavoritePlayer.user_id == user.id,
        FavoritePlayer.player_id == player_id,
    ))
    if fav is not None:
        db.delete(fav)
        db.commit()
    return Response(status_code=204)


@router.get("/notifications/preferences")
def get_preferences(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    pref = _pref(db, user.id)
    db.commit()
    return _pref_json(pref)


@router.patch("/notifications/preferences")
def update_preferences(
    data: PreferencesUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    pref = _pref(db, user.id)
    for key in data.model_fields_set:
        value = getattr(data, key)
        if key != "min_final_rating" and value is None:
            raise HTTPException(status_code=422, detail="Boolean preferences cannot be null")
        setattr(pref, key, value)
    db.commit()
    return _pref_json(pref)


@router.post("/notifications/devices", status_code=201)
def register_device(
    data: DeviceRegistration,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(PushDevice.id).where(
        PushDevice.registration_token == data.token,
        PushDevice.user_id == user.id,
    )):
        return {"registered": True}
    if db.scalar(select(PushDevice.id).where(
        PushDevice.registration_token == data.token,
    )):
        raise HTTPException(status_code=409, detail="Device already registered")
    db.add(PushDevice(user_id=user.id, registration_token=data.token))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Device already registered") from exc
    return {"registered": True}


@router.delete("/notifications/devices", status_code=204)
def remove_device(
    data: DeviceRegistration,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    device = db.scalar(select(PushDevice).where(
        PushDevice.user_id == user.id,
        PushDevice.registration_token == data.token,
    ))
    if device:
        db.delete(device)
        db.commit()
    return Response(status_code=204)


@router.get("/notifications")
def inbox(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    return [
        {
            "id": row.id, "type": row.kind, "player_id": row.player_id,
            "match_id": row.match_id, "data": row.payload,
            "created_at": row.created_at, "read_at": row.read_at,
            "invalidated_at": row.invalidated_at,
        }
        for row in db.scalars(statement)
    ]


@router.post("/notifications/{notification_id}/read")
def mark_read(
    notification_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    note = db.scalar(select(Notification).where(
        Notification.id == notification_id, Notification.user_id == user.id,
    ))
    if note is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if note.read_at is None:
        note.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"id": notification_id, "read": True}
