"""Users, favorites, notification preferences, inbox and optional push outbox.

Revision ID: 0004_favorites_notifications
Revises: 0003_player_stats_team
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_favorites_notifications"
down_revision = "0003_player_stats_team"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "favorite_players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "player_id"),
    )
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("goals", sa.Boolean(), nullable=False),
        sa.Column("assists", sa.Boolean(), nullable=False),
        sa.Column("final_rating", sa.Boolean(), nullable=False),
        sa.Column("min_final_rating", sa.Float()),
        sa.CheckConstraint("min_final_rating IS NULL OR "
                           "(min_final_rating >= 0 AND min_final_rating <= 10)"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "event_key"),
    )
    op.create_table(
        "push_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("registration_token", sa.String(512), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notification_id", sa.Integer(),
                  sa.ForeignKey("notifications.id"), nullable=False),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("push_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(255)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("notification_id", "device_id"),
        sa.CheckConstraint("attempts >= 0"),
    )


def downgrade() -> None:
    for table in (
        "push_deliveries", "push_devices", "notifications",
        "notification_preferences", "favorite_players", "users",
    ):
        op.drop_table(table)
