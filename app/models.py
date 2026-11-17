from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Competition(Base):
    __tablename__ = "competitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("provider", "provider_id"),)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("provider", "provider_id"),)


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date)
    nationality: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[str | None] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("provider", "provider_id"),)


class Match(Base):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int | None] = mapped_column(ForeignKey("competitions.id"))
    home_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    kickoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(25), default="scheduled")
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("provider", "provider_id"),
        CheckConstraint("home_score IS NULL OR home_score >= 0"),
        CheckConstraint("away_score IS NULL OR away_score >= 0"),
    )


class MatchEvent(Base):
    __tablename__ = "match_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    minute: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(120), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider", "match_id", "provider_id"),)


class PlayerMatchStats(Base):
    __tablename__ = "player_match_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    minutes: Mapped[int | None] = mapped_column(Integer)
    goals: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("match_id", "player_id"),)


class Standing(Base):
    __tablename__ = "standings"
    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    played: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("competition_id", "season", "team_id"),)


class ProviderQuota(Base):
    __tablename__ = "provider_quota"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("provider", "day"),)


class TeamMatchStat(Base):
    __tablename__ = "team_match_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )
    __table_args__ = (UniqueConstraint("match_id", "team_id", "name"),)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )


class FavoritePlayer(Base):
    __tablename__ = "favorite_players"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )
    __table_args__ = (UniqueConstraint("user_id", "player_id"),)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    goals: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assists: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    final_rating: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_final_rating: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (
        CheckConstraint("min_final_rating IS NULL OR "
                        "(min_final_rating >= 0 AND min_final_rating <= 10)"),
    )


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_event_id: Mapped[int | None] = mapped_column(ForeignKey("match_events.id"))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "event_key"),)


class PushDevice(Base):
    __tablename__ = "push_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    registration_token: Mapped[str] = mapped_column(
        String(512), nullable=False, unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )


class PushDelivery(Base):
    __tablename__ = "push_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id"), nullable=False,
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("push_devices.id", ondelete="CASCADE"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("notification_id", "device_id"),
        CheckConstraint("attempts >= 0"),
    )


class LiveUpdate(Base):
    __tablename__ = "live_updates"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_live_updates_match_id_id", "match_id", "id"),)


class HistoricalPlayerFetch(Base):
    """Receipt for a provider fixture/players response, NOT coverage certification."""

    __tablename__ = "historical_player_fetches"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id"), unique=True, nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    players_imported: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )
    __table_args__ = (
        CheckConstraint("status IN ('imported', 'empty')"),
        CheckConstraint("players_imported >= 0"),
    )


class ProviderRateLock(Base):
    """One database lock row serializes in-app provider reservations."""

    __tablename__ = "provider_rate_locks"
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)


class ProviderRequestLog(Base):
    """Rolling 60-second accounting of reserved HTTP calls."""

    __tablename__ = "provider_request_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index(
        "ix_provider_request_log_provider_reserved_at", "provider", "reserved_at",
    ),)


class LeagueCoverageProbe(Base):
    """Provider-declared feature hints, never proof of season completeness."""

    __tablename__ = "league_coverage_probes"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_league_id: Mapped[str] = mapped_column(String(100), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    reported_events: Mapped[bool | None] = mapped_column(Boolean)
    reported_statistics_fixtures: Mapped[bool | None] = mapped_column(Boolean)
    reported_statistics_players: Mapped[bool | None] = mapped_column(Boolean)
    reported_lineups: Mapped[bool | None] = mapped_column(Boolean)
    raw_coverage: Mapped[dict | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )
    __table_args__ = (
        UniqueConstraint("provider", "provider_league_id", "season"),
    )
