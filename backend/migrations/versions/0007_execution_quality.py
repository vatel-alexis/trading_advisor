"""execution quality: progressive limit orders, multi-signal stop, mid and natural quotes

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06 09:00:00

- positions keep the mid and natural quotes of their entry and of their exit decision;
- marks keep the natural cost to close (liquidation spread) and the underlying price;
- orders keep their step in the progressive limit policy and whether they were replaced;
- new event `order_repriced`.
The new parameters (stop signals, limit steps) take their defaults in every profile: profiles
store the parameters they were saved with, and missing keys take the defaults.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(precision=14, scale=4)
POSITION_COLUMNS = ("entry_mid", "entry_natural", "exit_mid", "exit_natural")
MARK_COLUMNS = ("natural", "underlying_price")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE position_event_type ADD VALUE IF NOT EXISTS 'order_repriced'")
    for name in POSITION_COLUMNS:
        op.add_column("positions", sa.Column(name, MONEY, nullable=True))
    for name in MARK_COLUMNS:
        op.add_column("position_marks", sa.Column(name, MONEY, nullable=True))
    op.add_column("orders", sa.Column("reprice_step", sa.Integer(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("replaced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("orders", "replaced")
    op.drop_column("orders", "reprice_step")
    for name in MARK_COLUMNS:
        op.drop_column("position_marks", name)
    for name in POSITION_COLUMNS:
        op.drop_column("positions", name)
