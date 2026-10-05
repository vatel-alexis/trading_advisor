"""strategy modes: Short Put Income apart from the True Wheel, 45-65 DTE entries

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05 22:00:00

- new strategy type `short_put` (Short Put Income: the put is bought back, at 21 DTE at the
  latest; `cash_secured_put` now means the True Wheel put, kept to assignment);
- the "Prudent" profile enters at 45-65 DTE with 21 days of holding at least, without the
  $20 strike cap; if it is the active profile, a new screener version carries the change;
- the "Actuel" profile keeps its former behaviour (no holding minimum, $20 cap);
- a separate "Expérimental : grandes valeurs" profile holds the large caps.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied values (a migration must not depend on code that changes later).
PRUDENT = {
    "dte_min": 45,
    "dte_max": 65,
    "min_holding_days": 21,
    "use_wheel_max_strike": False,
    "enable_true_wheel": True,
    "true_wheel": [],
}
PRUDENT_DESCRIPTION = (
    "Profil par défaut : ETF en put credit spreads et Short Put Income, entrée à 45-65 DTE "
    "(21 jours de détention au moins), delta 0.10-0.20, risque de 1 % du capital par trade, "
    "10 % de perte maximale ouverte, 5 % par cluster, stop mensuel 4 %."
)
ACTUEL = {"min_holding_days": 0, "use_wheel_max_strike": True}
EXPERIMENTAL = "Expérimental : grandes valeurs"
EXPERIMENTAL_DESCRIPTION = (
    "Mode expérimental, hors profils standards : put credit spreads sur les grandes valeurs "
    "seules. Déficitaire au backtest 2019-2026 (-11,8 %/an à 40-55 DTE)."
)
EXPERIMENTAL_PARAMS = {
    "enable_etfs": False,
    "enable_wheel": False,
    "enable_true_wheel": False,
    "enable_large_caps": True,
}
EXPERIMENTAL_REFERENCE = {
    "cagr": -0.118,
    "max_drawdown": 0.784,
    "profit_factor": 0.73,
    "start": "2019-01-02",
    "end": "2026-10-02",
    "source": "Backtest 2019-2026, grandes valeurs seules à 40-55 DTE (docs/backtest-resultats.md)",
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
configs = sa.table(
    "strategy_configs",
    sa.column("id", sa.Integer),
    sa.column("version", sa.Integer),
    sa.column("params", sa.JSON),
    sa.column("is_active", sa.Boolean),
    sa.column("profile_id", sa.Integer),
    sa.column("activation", sa.JSON),
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE strategy_type ADD VALUE IF NOT EXISTS 'short_put'")

    bind = op.get_bind()
    now = datetime.now(UTC)
    rows = {r.name: r for r in bind.execute(sa.select(profiles)).all()}
    if not rows:
        return  # the app builds the profiles from the current defaults on first use

    actuel = rows.get("Actuel")
    if actuel is not None:
        bind.execute(
            profiles.update()
            .where(profiles.c.id == actuel.id)
            .values(params={**(actuel.params or {}), **ACTUEL}, updated_at=now)
        )

    prudent = rows.get("Prudent")
    prudent_params = {**(prudent.params or {}), **PRUDENT} if prudent is not None else {}
    if EXPERIMENTAL not in rows:
        bind.execute(
            profiles.insert().values(
                name=EXPERIMENTAL,
                description=EXPERIMENTAL_DESCRIPTION,
                params={**prudent_params, **EXPERIMENTAL_PARAMS},
                created_at=now,
                updated_at=now,
                reference_summary=EXPERIMENTAL_REFERENCE,
            )
        )
    if prudent is None:
        return

    bind.execute(
        profiles.update()
        .where(profiles.c.id == prudent.id)
        .values(params=prudent_params, description=PRUDENT_DESCRIPTION, updated_at=now)
    )
    active = bind.execute(sa.select(configs).where(configs.c.is_active)).first()
    if active is not None and active.profile_id == prudent.id:
        version = bind.execute(sa.select(sa.func.max(configs.c.version))).scalar() or 0
        bind.execute(configs.update().values(is_active=False))
        bind.execute(
            configs.insert().values(
                version=version + 1,
                params=prudent_params,
                is_active=True,
                profile_id=prudent.id,
                activation={
                    "by": "migration 0005",
                    "note": "Entrée à 45-65 DTE, Short Put Income séparé de la True Wheel.",
                    "at": now.isoformat(),
                },
            )
        )


def downgrade() -> None:
    # Postgres cannot drop an enum value; the profiles keep their parameters.
    pass
