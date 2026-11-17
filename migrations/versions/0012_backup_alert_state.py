"""Deduplicate opt-in backup health webhook alerts across database workers.

Revision ID: 0012_backup_alert_state
Revises: 0011_match_outbox_index
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_backup_alert_state"
down_revision = "0011_match_outbox_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_alert_states",
        sa.Column("scope_hash", sa.String(64), primary_key=True),
        sa.Column("condition", sa.String(24), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "condition IN ('unknown', 'recent_unverified', 'missing', 'stale', 'error')"
        ),
    )


def downgrade() -> None:
    op.drop_table("backup_alert_states")
