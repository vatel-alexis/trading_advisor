from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Ratio


class AccountSnapshot(Base):
    """End-of-day account state; the source of the equity curve."""

    __tablename__ = "account_snapshots"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    equity: Mapped[Decimal]
    cash: Mapped[Decimal]
    collateral_used: Mapped[Decimal]
    buying_power: Mapped[Decimal]
    beta_weighted_delta: Mapped[Decimal | None]


class JobHeartbeat(Base):
    """Last run of each worker job, for the health checks that gate new entries."""

    __tablename__ = "job_heartbeats"

    job: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class IvHistory(Base):
    """Daily 30-day IV and realized volatility per underlying, collected for IV Rank."""

    __tablename__ = "iv_history"

    underlying: Mapped[str] = mapped_column(String(16), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal]
    iv30: Mapped[Decimal | None] = mapped_column(Ratio)
    hv30: Mapped[Decimal | None] = mapped_column(Ratio)
