"""Market data as the engines see it, and the volatility measures derived from it."""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.domain.pricing import OptionType

TRADING_DAYS = 252
HV_WINDOW = 30


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    option_type: OptionType
    expiration: date
    strike: float
    bid: float
    ask: float
    iv: float
    open_interest: int
    volume: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class MarketSnapshot:
    """One underlying at screening time: price history, next earnings and its option chain."""

    symbol: str
    spot: float
    # Daily closes, oldest first, about one year.
    closes: Sequence[float]
    options: Sequence[OptionQuote]
    next_earnings: date | None = None
    sector: str | None = None


@dataclass(frozen=True)
class IvRank:
    value: float
    # "history": rank of today's IV30 within stored IV30 values.
    # "hv_proxy": rank of today's IV30 within the past year of 30-day realized volatility.
    method: str
    days: int


def realized_vol_series(closes: Sequence[float], window: int = HV_WINDOW) -> list[float]:
    """Annualized rolling realized volatility of daily log returns."""
    prices = [c for c in closes if c > 0]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False)]
    return [
        statistics.stdev(returns[i - window : i]) * math.sqrt(TRADING_DAYS)
        for i in range(window, len(returns) + 1)
    ]


def hv30(closes: Sequence[float]) -> float | None:
    # Last value of realized_vol_series, without computing the whole year.
    series = realized_vol_series(closes[-(HV_WINDOW + 1) :])
    return series[-1] if series else None


def atm_iv30(snapshot: MarketSnapshot, today: date) -> float | None:
    """IV of the strike closest to spot, on the expiration closest to 30 days out."""
    quotes = [q for q in snapshot.options if q.iv > 0 and q.expiration > today]
    if not quotes:
        return None
    expiration = min({q.expiration for q in quotes}, key=lambda e: abs((e - today).days - 30))
    same_exp = [q for q in quotes if q.expiration == expiration]
    strike = min({q.strike for q in same_exp}, key=lambda k: abs(k - snapshot.spot))
    ivs = [q.iv for q in same_exp if q.strike == strike]
    return sum(ivs) / len(ivs)


def _rank(value: float, low: float, high: float) -> float:
    if high <= low:
        return 50.0
    return min(100.0, max(0.0, (value - low) / (high - low) * 100))


def iv_rank(
    current_iv: float,
    iv_history: Sequence[float],
    closes: Sequence[float],
    min_history: int,
) -> IvRank | None:
    """IV Rank from stored IV30 history, or from realized volatility while history is short.

    Yahoo has no IV history, so the app stores one IV30 per day from its first run. Until
    `min_history` days are stored, the rank uses the one-year range of 30-day realized
    volatility instead. IV usually sits above realized volatility, so the proxy reads high.
    """
    if len(iv_history) >= min_history:
        values = [*iv_history, current_iv]
        return IvRank(_rank(current_iv, min(values), max(values)), "history", len(iv_history))
    series = realized_vol_series(closes)
    if len(series) < 20:
        return None
    return IvRank(_rank(current_iv, min(series), max(series)), "hv_proxy", len(series))
