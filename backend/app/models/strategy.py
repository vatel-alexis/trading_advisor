from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Ratio, Timestamped
from app.models.enums import (
    DecisionAction,
    OpportunityStatus,
    OptionType,
    RejectReason,
    Side,
    StrategyType,
)


def pg_enum(enum_cls: type, name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class StrategyConfig(Timestamped, Base):
    """Versioned screener and risk parameters; each opportunity points to the version used."""

    __tablename__ = "strategy_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(default=False)
    # Settings profile this version was written from (None for versions made before profiles).
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_profiles.id", ondelete="SET NULL")
    )
    # How it was activated: the risk warnings shown and whether they were confirmed.
    activation: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ScreenerRun(Base):
    __tablename__ = "screener_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_config_id: Mapped[int] = mapped_column(ForeignKey("strategy_configs.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    universe_size: Mapped[int | None]
    # Contracts remaining after each filter, e.g. {"iv_rank": 412, "liquidity": 97, ...}.
    filter_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Opportunity(Timestamped, Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True)
    screener_run_id: Mapped[int] = mapped_column(ForeignKey("screener_runs.id"), index=True)
    strategy_config_id: Mapped[int] = mapped_column(ForeignKey("strategy_configs.id"))
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    sector: Mapped[str | None] = mapped_column(String(64))
    strategy_type: Mapped[StrategyType] = mapped_column(pg_enum(StrategyType, "strategy_type"))
    status: Mapped[OpportunityStatus] = mapped_column(
        pg_enum(OpportunityStatus, "opportunity_status"), default=OpportunityStatus.PROPOSED
    )
    expiration: Mapped[date] = mapped_column(Date)
    dte: Mapped[int]
    underlying_price: Mapped[Decimal]
    credit: Mapped[Decimal]
    max_loss: Mapped[Decimal]
    collateral: Mapped[Decimal]
    # Loss if the underlying gaps down by the stress move (total over the quantity).
    stress_loss: Mapped[Decimal | None]
    breakeven: Mapped[Decimal]
    short_delta: Mapped[Decimal] = mapped_column(Ratio)
    pop: Mapped[Decimal] = mapped_column(Ratio)
    iv_rank: Mapped[Decimal | None] = mapped_column(Ratio)
    iv_hv_ratio: Mapped[Decimal | None] = mapped_column(Ratio)
    aroc: Mapped[Decimal] = mapped_column(Ratio)
    score: Mapped[Decimal] = mapped_column(Ratio)
    next_earnings: Mapped[date | None] = mapped_column(Date)
    # Full snapshot of every metric at proposal time, for later bias analysis.
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    legs: Mapped[list["OpportunityLeg"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )
    decision: Mapped["Decision | None"] = relationship(back_populates="opportunity")


class OpportunityLeg(Base):
    __tablename__ = "opportunity_legs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    option_symbol: Mapped[str] = mapped_column(String(32))
    option_type: Mapped[OptionType] = mapped_column(pg_enum(OptionType, "option_type"))
    side: Mapped[Side] = mapped_column(pg_enum(Side, "side"))
    strike: Mapped[Decimal]
    quantity: Mapped[int]
    bid: Mapped[Decimal]
    ask: Mapped[Decimal]
    delta: Mapped[Decimal | None] = mapped_column(Ratio)
    open_interest: Mapped[int | None]
    volume: Mapped[int | None]

    opportunity: Mapped[Opportunity] = relationship(back_populates="legs")


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("opportunity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    action: Mapped[DecisionAction] = mapped_column(pg_enum(DecisionAction, "decision_action"))
    reject_reason: Mapped[RejectReason | None] = mapped_column(
        pg_enum(RejectReason, "reject_reason")
    )
    note: Mapped[str | None] = mapped_column(String(500))
    # Guards against a double click sending two orders.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    opportunity: Mapped[Opportunity] = relationship(back_populates="decision")


class ShadowOutcome(Base):
    """What a rejected opportunity would have returned, tracked as if it had been taken."""

    __tablename__ = "shadow_outcomes"
    __table_args__ = (UniqueConstraint("opportunity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    pnl: Mapped[Decimal | None]
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
