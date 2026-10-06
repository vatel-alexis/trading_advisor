"""Daily report of the PEA ETF portfolio (targets, alerts, backtests), computed by the worker."""

from datetime import date
from typing import Any

from sqlalchemy import JSON, Date
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class PeaReport(Timestamped, Base):
    """One computation of app.domain.pea.report(); the newest row is the one shown."""

    __tablename__ = "pea_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Last trading day in the prices, and the month-end whose signal sets the targets.
    as_of: Mapped[date] = mapped_column(Date, index=True)
    signal_day: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
