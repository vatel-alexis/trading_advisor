"""quality scores: return on risk instead of AROC, expiration cluster

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05 23:00:00

Profiles store every parameter, so the new defaults are written into them:
- "Prudent" filters on the return on risk (not annualized) instead of the AROC, and gets the
  expiration limit and the quality minimum; if it is active, a new screener version carries it;
- "Actuel" keeps its former filters (AROC, no quality minimum, no expiration limit).
The former score weights are dropped from both (unknown keys are ignored anyway).
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_WEIGHTS = ("score_weight_pop", "score_weight_aroc", "score_weight_iv_rank")
PRUDENT = {
    "use_ror_filter": True,
    "min_ror_spread": 0.10,
    "min_ror_put": 0.01,
    "use_aroc_filter": False,
    "max_expiration_risk_pct": 0.05,
    "min_quality_score": 0.30,
}
ACTUEL = {
    "use_ror_filter": False,
    "use_aroc_filter": True,
    "max_expiration_risk_pct": 0.50,
    "min_quality_score": 0.0,
}

profiles = sa.table(
    "strategy_profiles",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("params", sa.JSON),
    sa.column("updated_at", sa.DateTime(timezone=True)),
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


def _merge(params: dict | None, values: dict) -> dict:
    out = {k: v for k, v in (params or {}).items() if k not in OLD_WEIGHTS}
    return {**out, **values}


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    rows = {r.name: r for r in bind.execute(sa.select(profiles)).all()}

    actuel = rows.get("Actuel")
    if actuel is not None:
        bind.execute(
            profiles.update()
            .where(profiles.c.id == actuel.id)
            .values(params=_merge(actuel.params, ACTUEL), updated_at=now)
        )

    prudent = rows.get("Prudent")
    if prudent is None:
        return
    params = _merge(prudent.params, PRUDENT)
    bind.execute(
        profiles.update().where(profiles.c.id == prudent.id).values(params=params, updated_at=now)
    )
    active = bind.execute(sa.select(configs).where(configs.c.is_active)).first()
    if active is not None and active.profile_id == prudent.id:
        version = bind.execute(sa.select(sa.func.max(configs.c.version))).scalar() or 0
        bind.execute(configs.update().values(is_active=False))
        bind.execute(
            configs.insert().values(
                version=version + 1,
                params=params,
                is_active=True,
                profile_id=prudent.id,
                activation={
                    "by": "migration 0006",
                    "note": "Notes de qualité séparées, rendement sur risque non annualisé.",
                    "at": now.isoformat(),
                },
            )
        )


def downgrade() -> None:
    pass
