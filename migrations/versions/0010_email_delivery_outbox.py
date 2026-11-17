"""Encrypted one-time-code email delivery outbox.

Revision ID: 0010_email_delivery_outbox
Revises: 0009_account_recovery
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_email_delivery_outbox"
down_revision = "0009_account_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "token_id", sa.Integer(),
            sa.ForeignKey("auth_one_time_tokens.id"), nullable=False, unique=True,
        ),
        sa.Column("encrypted_code", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'sent', 'expired', 'failed')"),
        sa.CheckConstraint("attempts >= 0"),
    )
    op.create_index(
        "ix_email_deliveries_status_next_attempt",
        "email_deliveries", ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_deliveries_status_next_attempt",
        table_name="email_deliveries",
    )
    op.drop_table("email_deliveries")
