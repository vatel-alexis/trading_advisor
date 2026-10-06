"""PEA ETF portfolio reports

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06 22:30:00

One row per computation of the PEA page (targets per risk level, adjustment alerts and
backtests), written by the worker once a day after the Paris close.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pea_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("signal_day", sa.Date(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pea_reports")),
    )
    op.create_index(op.f("ix_pea_reports_as_of"), "pea_reports", ["as_of"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_pea_reports_as_of"), table_name="pea_reports")
    op.drop_table("pea_reports")
