"""Index pending match-outbox scans for worker and operations monitor.

Revision ID: 0011_match_outbox_index
Revises: 0010_email_delivery_outbox
"""
from alembic import op

revision = "0011_match_outbox_index"
down_revision = "0010_email_delivery_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_live_updates_published_created",
        "live_updates", ["published_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_live_updates_published_created", table_name="live_updates",
    )
