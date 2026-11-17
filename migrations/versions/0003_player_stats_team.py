"""Associate per-match player statistics with their team.

Revision ID: 0003_player_stats_team
Revises: 0002_team_match_statistics
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_player_stats_team"
down_revision = "0002_team_match_statistics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("player_match_stats") as batch:
        batch.add_column(sa.Column("team_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_player_match_stats_team_id_teams", "teams", ["team_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("player_match_stats") as batch:
        batch.drop_constraint("fk_player_match_stats_team_id_teams", type_="foreignkey")
        batch.drop_column("team_id")
