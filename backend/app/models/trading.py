from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Ratio, Timestamped
from app.models.enums import (
    ExitReason,
    InstrumentType,
    OptionType,
    OrderPurpose,
    OrderStatus,
    PositionEventType,
    PositionStatus,
    Side,
    StrategyType,
)
from app.models.strategy import pg_enum


class Position(Timestamped, Base):
    """A strategy-level position (a spread, a CSP, shares from assignment, a covered call)."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    # Wheel chain: assigned shares point to the CSP, a covered call points to the shares.
    parent_position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"))
    underlying: Mapped[str] = mapped_column(String(16), index=True)
    sector: Mapped[str | None] = mapped_column(String(64))
    strategy_type: Mapped[StrategyType | None] = mapped_column(
        pg_enum(StrategyType, "strategy_type")
    )
    status: Mapped[PositionStatus] = mapped_column(
        pg_enum(PositionStatus, "position_status"), default=PositionStatus.PENDING
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_credit: Mapped[Decimal | None]
    exit_debit: Mapped[Decimal | None]
    collateral: Mapped[Decimal]
    max_loss: Mapped[Decimal | None]
    stress_loss: Mapped[Decimal | None]
    realized_pnl: Mapped[Decimal | None]
    # Net quotes per share when the position was accepted (credit) and when its exit was
    # decided (cost to close), to compare the fills with the mid and the natural price.
    entry_mid: Mapped[Decimal | None]
    entry_natural: Mapped[Decimal | None]
    exit_mid: Mapped[Decimal | None]
    exit_natural: Mapped[Decimal | None]
    exit_reason: Mapped[ExitReason | None] = mapped_column(pg_enum(ExitReason, "exit_reason"))

    legs: Mapped[list["PositionLeg"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )
    events: Mapped[list["PositionEvent"]] = relationship(back_populates="position")
    orders: Mapped[list["Order"]] = relationship(back_populates="position")


class PositionLeg(Base):
    __tablename__ = "position_legs"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    instrument_type: Mapped[InstrumentType] = mapped_column(
        pg_enum(InstrumentType, "instrument_type")
    )
    symbol: Mapped[str] = mapped_column(String(32))
    option_type: Mapped[OptionType | None] = mapped_column(pg_enum(OptionType, "option_type"))
    strike: Mapped[Decimal | None]
    expiration: Mapped[date | None] = mapped_column(Date)
    side: Mapped[Side] = mapped_column(pg_enum(Side, "side"))
    quantity: Mapped[int]
    avg_price: Mapped[Decimal | None]

    position: Mapped[Position] = relationship(back_populates="legs")


class Order(Timestamped, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[OrderPurpose] = mapped_column(pg_enum(OrderPurpose, "order_purpose"))
    status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus, "order_status"), default=OrderStatus.NEW
    )
    order_type: Mapped[str] = mapped_column(String(16))
    time_in_force: Mapped[str] = mapped_column(String(8))
    limit_price: Mapped[Decimal | None]
    # Net quote at submission, to measure how optimistic paper fills are.
    quote_bid: Mapped[Decimal | None]
    quote_ask: Mapped[Decimal | None]
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Progressive limit: the step of this try (None: fixed price), and whether it was canceled
    # to be sent again one step closer to the natural price.
    reprice_step: Mapped[int | None]
    replaced: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    position: Mapped[Position] = relationship(back_populates="orders")
    fills: Mapped[list["Fill"]] = relationship(back_populates="order")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[Side] = mapped_column(pg_enum(Side, "side"))
    quantity: Mapped[int]
    price: Mapped[Decimal]
    fees: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="fills")


class PositionEvent(Base):
    __tablename__ = "position_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    type: Mapped[PositionEventType] = mapped_column(
        pg_enum(PositionEventType, "position_event_type")
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    position: Mapped[Position] = relationship(back_populates="events")


class PositionMark(Base):
    __tablename__ = "position_marks"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mark: Mapped[Decimal]
    delta: Mapped[Decimal | None] = mapped_column(Ratio)
    unrealized_pnl: Mapped[Decimal]
    # Cost to close at the natural price (natural - mark is the liquidation spread).
    natural: Mapped[Decimal | None]
    underlying_price: Mapped[Decimal | None]
