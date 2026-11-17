"""Email verification, revocable sessions and one-time password reset codes.

Revision ID: 0009_account_recovery
Revises: 0008_auth_rate_bucket
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_account_recovery"
down_revision = "0008_auth_rate_bucket"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column(
            "session_version", sa.Integer(), nullable=False, server_default="0",
        ))
    op.create_table(
        "auth_one_time_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')"),
    )
    op.create_index(
        "ix_auth_tokens_user_purpose",
        "auth_one_time_tokens", ["user_id", "purpose"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_tokens_user_purpose", table_name="auth_one_time_tokens")
    op.drop_table("auth_one_time_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("session_version")
        batch.drop_column("email_verified_at")
