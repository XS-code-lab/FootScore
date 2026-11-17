"""Per-client hashed auth attempt buckets, to limit abuse without Redis.

Revision ID: 0008_auth_rate_bucket
Revises: 0007_provider_guardrails
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_auth_rate_bucket"
down_revision = "0007_provider_guardrails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_buckets",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("used >= 0"),
    )


def downgrade() -> None:
    op.drop_table("auth_rate_buckets")
