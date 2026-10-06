"""profile "Short Put Income 40 %" from the 2026-10-06 parameter search

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 10:30:00

Adds the profile chosen by the parameter search (docs/recherche-parametres.md): the Prudent
parameters with the ETF spreads off, delta 0.15-0.25, a 75 % profit target and a 40 % open
max loss. It is only offered: the active profile and screener version do not change.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied values (a migration must not depend on code that changes later).
NAME = "Short Put Income 40 %"
DESCRIPTION = (
    "Recherche de paramètres du 06/10/2026 : Short Put Income seul (spreads ETF déficitaires "
    "dans toutes les variantes), delta 0.15-0.25, objectif de gain 75 %, perte maximale "
    "ouverte 40 % (un choc de -30 % sur toutes les positions reste sous 10 % du capital). "
    "Autres garde-fous inchangés. Voir docs/recherche-parametres.md."
)
OVERRIDES = {
    "enable_etfs": False,
    "delta_min": 0.15,
    "delta_max": 0.25,
    "take_profit_pct": 0.75,
    "max_open_risk_pct": 0.40,
}
REFERENCE = {
    "cagr": 0.046,
    "max_drawdown": 0.047,
    "profit_factor": 2.34,
    "start": "2019-01-02",
    "end": "2026-10-02",
    "source": "Backtest 2019-2026, exécution réaliste (docs/recherche-parametres.md)",
}

profiles = sa.table(
    "strategy_profiles",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("description", sa.String),
    sa.column("params", sa.JSON),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    sa.column("reference_summary", sa.JSON),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = {r.name: r for r in bind.execute(sa.select(profiles)).all()}
    prudent = rows.get("Prudent")
    if prudent is None or NAME in rows:
        return  # without profiles yet, the app builds them (this one included) on first use
    now = datetime.now(UTC)
    bind.execute(
        profiles.insert().values(
            name=NAME,
            description=DESCRIPTION,
            params={**(prudent.params or {}), **OVERRIDES},
            created_at=now,
            updated_at=now,
            reference_summary=REFERENCE,
        )
    )


def downgrade() -> None:
    op.execute(sa.delete(profiles).where(profiles.c.name == NAME))
