"""safety: worker heartbeats, stress loss, profile risk verdicts, Prudent active by default

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05 20:00:00

Data changes, applied by the worker's `alembic upgrade head` on the hosted database:
- the "Prudent" profile gets the new risk and safety limits and becomes the active profile
  (a new strategy_configs version); positions already open keep the version they opened with;
- the "Actuel" profile is described as the former, loss-making defaults, with its documented
  2019-2026 backtest, so activating it asks for an explicit confirmation;
- without any profile, the active version is retired so the app recreates it from the new
  defaults.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Values of app.domain.params.StrategyParams at this revision (copied: a migration must not
# depend on code that changes later).
PRUDENT = {
    "enable_large_caps": False,
    "dte_min": 40,
    "dte_max": 55,
    "delta_min": 0.10,
    "delta_max": 0.20,
    "spread_widths": [5.0, 2.5, 2.0, 1.0],
    "min_credit": 0.15,
    "max_trade_risk_pct": 0.01,
    "use_exceptional_risk": False,
    "exceptional_trade_risk_pct": 0.02,
    "exceptional_min_score": 0.85,
    "max_open_risk_pct": 0.10,
    "max_cluster_risk_pct": 0.05,
    "stress_move_etf": 0.15,
    "stress_move_stock": 0.30,
    "use_sector_limit": True,
    "max_daily_loss_pct": 0.02,
    "max_monthly_loss_pct": 0.04,
    "max_drawdown_pct": 0.10,
    "max_monitor_age_minutes": 30,
    "max_worker_age_minutes": 60,
    "max_credit_drift_pct": 0.25,
}
PRUDENT_DESCRIPTION = (
    "Profil par défaut : ETF + wheel, entrée à 40-55 DTE, delta 0.10-0.20, risque de 1 % du "
    "capital par trade, 10 % de perte maximale ouverte, 5 % par cluster, stop mensuel 4 %."
)
ACTUEL_DESCRIPTION = (
    "Anciens réglages par défaut, déficitaires au backtest 2019-2026 (-19,1 %/an, drawdown "
    "86,9 %). Déconseillé : son activation demande une confirmation."
)
ACTUEL_REFERENCE = {
    "cagr": -0.191,
    "max_drawdown": 0.869,
    "profit_factor": 0.74,
    "start": "2019-01-02",
    "end": "2026-10-02",
    "source": "Backtest 2019-2026 des anciens réglages par défaut (docs/backtest-resultats.md)",
}

profiles = sa.table(
    "strategy_profiles",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("description", sa.String),
    sa.column("params", sa.JSON),
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
    op.create_table(
        "job_heartbeats",
        sa.Column("job", sa.String(length=32), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("job", name=op.f("pk_job_heartbeats")),
    )
    op.add_column("strategy_profiles", sa.Column("reference_summary", sa.JSON(), nullable=True))
    op.add_column("strategy_configs", sa.Column("activation", sa.JSON(), nullable=True))
    op.add_column(
        "opportunities",
        sa.Column("stress_loss", sa.Numeric(precision=14, scale=4), nullable=True),
    )
    op.add_column(
        "positions", sa.Column("stress_loss", sa.Numeric(precision=14, scale=4), nullable=True)
    )

    bind = op.get_bind()
    now = datetime.now(UTC)
    rows = {r.name: r for r in bind.execute(sa.select(profiles)).all()}

    actuel = rows.get("Actuel")
    if actuel is not None:
        bind.execute(
            profiles.update()
            .where(profiles.c.id == actuel.id)
            .values(description=ACTUEL_DESCRIPTION, reference_summary=ACTUEL_REFERENCE)
        )

    prudent = rows.get("Prudent")
    if prudent is not None:
        params = {**(prudent.params or {}), **PRUDENT}
        params.pop("sector_limit_includes_open", None)
        bind.execute(
            profiles.update()
            .where(profiles.c.id == prudent.id)
            .values(params=params, description=PRUDENT_DESCRIPTION, updated_at=now)
        )
        version = bind.execute(sa.select(sa.func.max(configs.c.version))).scalar() or 0
        bind.execute(configs.update().values(is_active=False))
        bind.execute(
            configs.insert().values(
                version=version + 1,
                params=params,
                is_active=True,
                profile_id=prudent.id,
                activation={
                    "by": "migration 0004",
                    "note": "Profil Prudent activé par défaut à la place d'Actuel.",
                    "at": now.isoformat(),
                },
            )
        )
    elif not rows:
        # No profile yet: the next screener run recreates the active version from the new
        # defaults, and the first visit of the settings page builds the profiles from it.
        bind.execute(configs.update().values(is_active=False))


def downgrade() -> None:
    op.drop_column("positions", "stress_loss")
    op.drop_column("opportunities", "stress_loss")
    op.drop_column("strategy_configs", "activation")
    op.drop_column("strategy_profiles", "reference_summary")
    op.drop_table("job_heartbeats")
