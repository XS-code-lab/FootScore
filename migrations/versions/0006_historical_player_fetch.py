"""Track successful fixture-player API responses without claiming completeness.

Revision ID: 0006_historical_player_fetch
Revises: 0005_live_reconciliation
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_historical_player_fetch"
down_revision = "0005_live_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_player_fetches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "match_id", sa.Integer(), sa.ForeignKey("matches.id"),
            nullable=False, unique=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("players_imported", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('imported', 'empty')"),
        sa.CheckConstraint("players_imported >= 0"),
    )


def downgrade() -> None:
    op.drop_table("historical_player_fetches")
