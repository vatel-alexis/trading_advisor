"""Settings profiles and backtest runs launched from the interface."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class StrategyProfile(Timestamped, Base):
    """A named set of strategy parameters, edited in the settings page.

    The live screener runs on the active strategy_configs version; activating a profile (or
    saving the active one) writes a new version with the profile's parameters, so every
    opportunity keeps pointing to the exact parameters it was screened with.
    """

    __tablename__ = "strategy_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(500))
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Documented backtest of these exact parameters (cagr, max_drawdown, source), used for the
    # activation warning until a backtest run of the profile exists; cleared on any change.
    reference_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class BacktestRun(Timestamped, Base):
    """One backtest, queued by the API and executed by the worker."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_profiles.id", ondelete="SET NULL"), index=True
    )
    # Copied at launch: the profile can change or be deleted afterwards.
    profile_name: Mapped[str] = mapped_column(String(80))
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    # ModelConfig overrides (reconstruction assumptions); empty means the calibrated defaults.
    model: Mapped[dict[str, Any]] = mapped_column(JSON)
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    capital: Mapped[float] = mapped_column(Float)
    refresh_data: Mapped[bool] = mapped_column(default=False)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(16), index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    step: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Headline figures (CAGR, drawdown, ...), listed and compared without loading the rest.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Equity curve, benchmark, breakdowns, funnel and trades.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class MarketHistoryCache(Base):
    """Daily history for the backtests (gzip of MarketHistory.to_json()); the newest row wins."""

    __tablename__ = "market_history_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_day: Mapped[date] = mapped_column(Date)
    last_day: Mapped[date] = mapped_column(Date)
    symbols: Mapped[list[str]] = mapped_column(JSON)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
