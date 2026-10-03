from datetime import date
from decimal import Decimal

from sqlalchemy import Date, String
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


class IvHistory(Base):
    """Daily 30-day IV and realized volatility per underlying, collected for IV Rank."""

    __tablename__ = "iv_history"

    underlying: Mapped[str] = mapped_column(String(16), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal]
    iv30: Mapped[Decimal | None] = mapped_column(Ratio)
    hv30: Mapped[Decimal | None] = mapped_column(Ratio)
