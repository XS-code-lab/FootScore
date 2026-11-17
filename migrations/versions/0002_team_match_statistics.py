"""Persist per-team fixture statistics.

Revision ID: 0002_team_match_statistics
Revises: 0001_initial
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_team_match_statistics"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_match_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("match_id", "team_id", "name"),
    )


def downgrade() -> None:
    op.drop_table("team_match_stats")
