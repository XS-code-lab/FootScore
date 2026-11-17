"""Frozen initial schema, independent from later ORM model changes.

Revision ID: 0001_initial
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("country", sa.String(100)),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "provider_id"),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "provider_id"),
    )
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("birth_date", sa.Date()),
        sa.Column("nationality", sa.String(100)),
        sa.Column("position", sa.String(50)),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "provider_id"),
    )
    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("competition_id", sa.Integer(), sa.ForeignKey("competitions.id")),
        sa.Column("home_team_id", sa.Integer(), sa.ForeignKey("teams.id")),
        sa.Column("away_team_id", sa.Integer(), sa.ForeignKey("teams.id")),
        sa.Column("kickoff", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(25)),
        sa.Column("home_score", sa.Integer()),
        sa.Column("away_score", sa.Integer()),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "provider_id"),
        sa.CheckConstraint("home_score IS NULL OR home_score >= 0"),
        sa.CheckConstraint("away_score IS NULL OR away_score >= 0"),
    )
    op.create_table(
        "match_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id")),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("minute", sa.Integer()),
        sa.Column("payload", sa.JSON()),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_id", sa.String(120), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "match_id", "provider_id"),
    )
    op.create_table(
        "player_match_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("minutes", sa.Integer()),
        sa.Column("goals", sa.Integer()),
        sa.Column("assists", sa.Integer()),
        sa.Column("rating", sa.Float()),
        sa.Column("payload", sa.JSON()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("match_id", "player_id"),
    )
    op.create_table(
        "standings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("competition_id", sa.Integer(), sa.ForeignKey("competitions.id"),
                  nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer()),
        sa.Column("played", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("competition_id", "season", "team_id"),
    )
    op.create_table(
        "provider_quota",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.UniqueConstraint("provider", "day"),
    )


def downgrade() -> None:
    for name in (
        "provider_quota", "standings", "player_match_stats", "match_events",
        "matches", "players", "teams", "competitions",
    ):
        op.drop_table(name)
