"""order lifecycle enum values

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 08:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE position_status ADD VALUE IF NOT EXISTS 'canceled'")
    op.execute("ALTER TYPE position_event_type ADD VALUE IF NOT EXISTS 'order_rejected'")


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value; the 0001 downgrade drops the types entirely.
    pass
