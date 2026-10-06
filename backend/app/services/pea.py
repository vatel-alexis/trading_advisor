"""PEA ETF portfolio: daily refresh by the worker, reads by the API."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain import pea
from app.models import PeaReport

# Reports older than this are deleted (each row holds ~70 kB of curves and history).
KEEP_DAYS = 45
# The banner reminds of a new allocation for this many days after the month-end signal.
ALERT_DAYS = 10


def fetch_prices(
    client,
) -> tuple[dict[str, dict[date, float]], dict[date, float]]:  # pragma: no cover - network
    """Daily adjusted closes of the PEA ETFs and their proxies, and EUR/USD, from Yahoo."""
    symbols = {a.ticker for a in pea.ASSETS} | {a.proxy for a in pea.ASSETS if a.proxy}
    start = int(datetime(pea.DATA_START.year - 1, 1, 1, tzinfo=UTC).timestamp())
    out: dict[str, dict[date, float]] = {}
    for symbol in sorted(symbols | {pea.FX_TICKER}):
        data = client.json(
            f"/v8/finance/chart/{symbol}",
            period1=start,
            period2=int(datetime.now(UTC).timestamp()),
            interval="1d",
            events="div,split",
            includeAdjustedClose="true",
        )["chart"]["result"][0]
        indicators = data["indicators"]
        closes = (indicators.get("adjclose") or [{}])[0].get("adjclose") or indicators["quote"][0][
            "close"
        ]
        out[symbol] = {
            datetime.fromtimestamp(ts, UTC).date(): float(c)
            for ts, c in zip(data.get("timestamp") or [], closes, strict=False)
            if c
        }
    fx = out.pop(pea.FX_TICKER)
    return out, fx


def build_report(
    prices: dict[str, dict[date, float]], fx: dict[date, float], today: date
) -> dict[str, Any]:
    days, index = pea.euro_indexes(prices, fx)
    return pea.report(days, index, today)


def store(session: Session, payload: dict[str, Any]) -> PeaReport:
    """Save a report and drop the old ones (the caller commits)."""
    row = PeaReport(
        as_of=date.fromisoformat(payload["as_of"]),
        signal_day=date.fromisoformat(payload["signal_day"]),
        payload=payload,
    )
    session.add(row)
    session.flush()
    cutoff = row.as_of - timedelta(days=KEEP_DAYS)
    session.execute(delete(PeaReport).where(PeaReport.as_of < cutoff))
    return row


def latest(session: Session) -> PeaReport | None:
    return session.execute(
        select(PeaReport).order_by(PeaReport.as_of.desc(), PeaReport.id.desc()).limit(1)
    ).scalar_one_or_none()


def last_computed(session: Session) -> datetime | None:
    row = latest(session)
    return row.created_at if row else None


def view(session: Session) -> dict[str, Any]:
    row = latest(session)
    if row is None:
        return {"report": None, "computed_at": None}
    return {"report": row.payload, "computed_at": row.created_at.isoformat()}


def alert(session: Session, today: date) -> dict[str, Any]:
    """The small banner: levels whose allocation changed at the last month-end, for a few days,
    and levels whose provisional signal (today's prices) would change at the coming one."""
    row = latest(session)
    if row is None:
        return {"active": False, "levels": [], "provisional": []}
    payload = row.payload
    signal_day = date.fromisoformat(payload["signal_day"])
    recent = (today - signal_day).days <= ALERT_DAYS
    levels = [lv["label"] for lv in payload["levels"] if lv["changes"]] if recent else []
    provisional = [lv["label"] for lv in payload["levels"] if lv["preview_changes"]]
    return {
        "active": bool(levels),
        "signal_day": payload["signal_day"],
        "levels": levels,
        "provisional": provisional,
        "provisional_day": payload.get("provisional_day"),
    }
