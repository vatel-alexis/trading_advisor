"""settings profiles, backtest runs and market history cache

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_profiles")),
        sa.UniqueConstraint("name", name=op.f("uq_strategy_profiles_name")),
    )
    op.add_column("strategy_configs", sa.Column("profile_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_strategy_configs_profile_id_strategy_profiles"),
        "strategy_configs",
        "strategy_profiles",
        ["profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("profile_name", sa.String(length=80), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("model", sa.JSON(), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("capital", sa.Float(), nullable=False),
        sa.Column("refresh_data", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("step", sa.String(length=120), nullable=True),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["strategy_profiles.id"],
            name=op.f("fk_backtest_runs_profile_id_strategy_profiles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_runs")),
    )
    op.create_index(
        op.f("ix_backtest_runs_profile_id"), "backtest_runs", ["profile_id"], unique=False
    )
    op.create_index(op.f("ix_backtest_runs_status"), "backtest_runs", ["status"], unique=False)
    op.create_table(
        "market_history_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_day", sa.Date(), nullable=False),
        sa.Column("last_day", sa.Date(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_history_cache")),
    )


def downgrade() -> None:
    op.drop_table("market_history_cache")
    op.drop_index(op.f("ix_backtest_runs_status"), table_name="backtest_runs")
    op.drop_index(op.f("ix_backtest_runs_profile_id"), table_name="backtest_runs")
    op.drop_table("backtest_runs")
    op.drop_constraint(
        op.f("fk_strategy_configs_profile_id_strategy_profiles"),
        "strategy_configs",
        type_="foreignkey",
    )
    op.drop_column("strategy_configs", "profile_id")
    op.drop_table("strategy_profiles")
