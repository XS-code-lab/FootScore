"""Per-provider sliding-minute reservations and advisory league metadata.

Revision ID: 0007_provider_guardrails
Revises: 0006_historical_player_fetch
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_provider_guardrails"
down_revision = "0006_historical_player_fetch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_rate_locks",
        sa.Column("provider", sa.String(40), primary_key=True),
    )
    op.create_table(
        "provider_request_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_provider_request_log_provider_reserved_at",
        "provider_request_log", ["provider", "reserved_at"],
    )
    op.create_table(
        "league_coverage_probes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_league_id", sa.String(100), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("reported_events", sa.Boolean()),
        sa.Column("reported_statistics_fixtures", sa.Boolean()),
        sa.Column("reported_statistics_players", sa.Boolean()),
        sa.Column("reported_lineups", sa.Boolean()),
        sa.Column("raw_coverage", sa.JSON()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_league_id", "season"),
    )


def downgrade() -> None:
    op.drop_table("league_coverage_probes")
    op.drop_index(
        "ix_provider_request_log_provider_reserved_at",
        table_name="provider_request_log",
    )
    op.drop_table("provider_request_log")
    op.drop_table("provider_rate_locks")
