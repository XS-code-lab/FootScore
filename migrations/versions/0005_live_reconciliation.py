"""Event reconciliation, notification retractions and durable live updates.

Revision ID: 0005_live_reconciliation
Revises: 0004_favorites_notifications
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_live_reconciliation"
down_revision = "0004_favorites_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("match_events") as batch:
        batch.add_column(sa.Column("is_active", sa.Boolean(), server_default=sa.true(),
                                   nullable=False))
        batch.add_column(sa.Column("retracted_at", sa.DateTime(timezone=True)))
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("source_event_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notifications_source_event_id_match_events",
            "match_events", ["source_event_id"], ["id"],
        )
        batch.add_column(sa.Column("invalidated_at", sa.DateTime(timezone=True)))
    op.create_table(
        "live_updates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_live_updates_match_id_id", "live_updates", ["match_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_live_updates_match_id_id", table_name="live_updates")
    op.drop_table("live_updates")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_column("invalidated_at")
        batch.drop_constraint(
            "fk_notifications_source_event_id_match_events", type_="foreignkey",
        )
        batch.drop_column("source_event_id")
    with op.batch_alter_table("match_events") as batch:
        batch.drop_column("retracted_at")
        batch.drop_column("is_active")
