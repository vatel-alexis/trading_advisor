"""Reconstructed option chains: Black-Scholes prices from a daily volatility proxy and a skew.

ATM implied volatility comes from VIX / VXN for SPY / QQQ, and from 30-day realized volatility
times a premium for every other name, with earnings-day moves left out so the proxy does not
jump after a report. Out-of-the-money puts get a linear skew in log-moneyness per sqrt(year).
"""

import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta

from app.backtest.data import VOL_INDEX, MarketHistory, SymbolHistory
from app.domain.market import TRADING_DAYS, MarketSnapshot, OptionQuote
from app.domain.params import StrategyParams
from app.domain.pricing import bs_put_price

HV_DAYS = 30
MIN_HISTORY_DAYS = 60
LIQUID = 10_000  # open interest and volume of synthetic quotes: liquidity is not modeled


@dataclass(frozen=True)
class ModelConfig:
    """Assumptions of the reconstruction, calibrated on the 2026-10-02 Yahoo chains."""

    # ATM IV = index level x ratio for SPY/QQQ (VIX carries skew and sits above ATM IV).
    index_atm_ratio: float = 0.85
    # ATM IV = 30-day realized volatility (earnings excluded) x premium for other names.
    hv_premium_etf: float = 1.2
    hv_premium_stock: float = 1.2
    min_atm_iv: float = 0.05
    # IV(K) = ATM + skew * ln(S/K) / sqrt(T): about 0.25 measured on SPY/QQQ, 0.18 on IWM.
    skew_etf: float = 0.25
    skew_stock: float = 0.15
    # Half bid/ask spread as a share of the mid (medians of 0.10-0.35 delta puts on Yahoo,
    # 2026-10-02; wheel names capped so their liquid strikes pass the 15 % spread filter).
    half_spread_etf: float = 0.008
    half_spread_large_cap: float = 0.05
    half_spread_wheel: float = 0.06
    min_half_spread: float = 0.005
    # Fills, as a fraction of the half spread paid away from the mid. The realistic default:
    # entries start at the mid and step toward the natural (half of it paid on average);
    # stops and time exits pay the whole half spread; the profit target is a resting limit
    # and fills at its price. See EXECUTION_SCENARIOS for the optimistic and pessimistic ones.
    entry_slippage: float = 0.5
    exit_slippage: float = 1.0
    # Bid/ask spreads widened (stress) or narrowed against the calibrated ones.
    spread_multiplier: float = 1.0
    rate: float = 0.04


# Execution scenarios: (label, overrides of the fills and spreads). The realistic one is the
# default model and the one shown first.
REALISTIC = "realiste"
EXECUTION_SCENARIOS: dict[str, tuple[str, dict[str, float]]] = {
    "optimiste": (
        "Optimiste : entrées au mid, sorties à mi-chemin du naturel",
        {"entry_slippage": 0.0, "exit_slippage": 0.5, "spread_multiplier": 1.0},
    ),
    REALISTIC: (
        "Réaliste : entrées à mi-chemin du naturel, stops et sorties au naturel",
        {"entry_slippage": 0.5, "exit_slippage": 1.0, "spread_multiplier": 1.0},
    ),
    "pessimiste": (
        "Pessimiste : entrées au naturel, sorties au-delà du naturel, écarts x1,5",
        {"entry_slippage": 1.0, "exit_slippage": 1.5, "spread_multiplier": 1.5},
    ),
}


def realized_vol(returns: list[float]) -> float:
    """Annualized sample standard deviation (statistics.stdev is exact but slow)."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = math.fsum(returns) / n
    variance = math.fsum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(variance * TRADING_DAYS)


@dataclass
class SymbolSeries:
    """One underlying aligned on its own trading days, with its ATM IV proxy per day."""

    history: SymbolHistory
    atm_iv: list[float | None]
    is_etf: bool
    half_spread_pct: float

    def index_of(self, day: date) -> int | None:
        i = bisect_left(self.history.dates, day)
        if i < len(self.history.dates) and self.history.dates[i] == day:
            return i
        return None

    def spot(self, i: int) -> float:
        return self.history.closes[i] * self.history.split_factors[i]

    def next_earnings(self, day: date) -> date | None:
        dates = self.history.earnings
        i = bisect_left(dates, day)
        return dates[i] if i < len(dates) else None


def ex_earnings_hv(h: SymbolHistory, window: int = HV_DAYS) -> list[float | None]:
    """Rolling realized volatility without the returns that span an earnings report."""
    out: list[float | None] = [None]
    kept: list[float] = []
    earnings = h.earnings
    for i in range(1, len(h.dates)):
        j = bisect_left(earnings, h.dates[i - 1])
        if not (j < len(earnings) and earnings[j] <= h.dates[i]):
            kept.append(math.log(h.closes[i] / h.closes[i - 1]))
            kept = kept[-window:]
        out.append(realized_vol(kept) if len(kept) >= window // 2 else None)
    return out


def build_series(
    market: MarketHistory, params: StrategyParams, cfg: ModelConfig
) -> dict[str, SymbolSeries]:
    spreads = {
        "etf": cfg.half_spread_etf,
        "large_cap": cfg.half_spread_large_cap,
        "short_put": cfg.half_spread_wheel,
        "true_wheel": cfg.half_spread_wheel,
    }
    series: dict[str, SymbolSeries] = {}
    for symbol, h in market.symbols.items():
        group = params.group_of(symbol)
        if group is None:
            continue
        is_etf = group == "etf"
        index = market.vol_indices.get(VOL_INDEX.get(symbol, ""), {})
        hv = ex_earnings_hv(h)
        premium = cfg.hv_premium_etf if is_etf else cfg.hv_premium_stock
        atm: list[float | None] = []
        for day, vol in zip(h.dates, hv, strict=True):
            if day in index:
                atm.append(max(cfg.min_atm_iv, index[day] * cfg.index_atm_ratio))
            elif vol is not None:
                atm.append(max(cfg.min_atm_iv, vol * premium))
            else:
                atm.append(None)
        series[symbol] = SymbolSeries(h, atm, is_etf, spreads[group] * cfg.spread_multiplier)
    return series


def strike_step(spot: float, is_etf: bool) -> float:
    if is_etf:
        return 1.0
    if spot < 25:
        return 0.5
    if spot < 100:
        return 1.0
    if spot < 300:
        return 2.5
    return 5.0


def expirations(day: date, dte_min: int, dte_max: int, monthly_only: bool) -> list[date]:
    """Fridays in the DTE window; only third Fridays for names without weeklies."""
    first = day + timedelta(days=dte_min)
    first += timedelta(days=(4 - first.weekday()) % 7)
    out = []
    exp = first
    while (exp - day).days <= dte_max:
        if not monthly_only or 15 <= exp.day <= 21:
            out.append(exp)
        exp += timedelta(days=7)
    return out


def put_iv(spot: float, strike: float, years: float, atm: float, skew: float) -> float:
    iv = atm + skew * math.log(spot / strike) / math.sqrt(years)
    return max(atm * 0.5, iv)


def put_mid(spot: float, strike: float, years: float, atm: float, skew: float, rate: float):
    return bs_put_price(spot, strike, years, put_iv(spot, strike, years, atm, skew), rate)


def call_mid(spot: float, strike: float, years: float, atm: float, skew: float, rate: float):
    """Call at the same skewed volatility as the put of its strike (put-call parity)."""
    if years <= 0:
        return max(0.0, spot - strike)
    put = put_mid(spot, strike, years, atm, skew, rate)
    return max(0.0, put + spot - strike * math.exp(-rate * years))


def option_mid(
    kind: str, spot: float, strike: float, years: float, atm: float, skew: float, rate: float
) -> float:
    if years <= 0:
        return max(0.0, strike - spot) if kind == "put" else max(0.0, spot - strike)
    if kind == "call":
        return call_mid(spot, strike, years, atm, skew, rate)
    return put_mid(spot, strike, years, atm, skew, rate)


def half_spread(mid: float, pct: float, cfg: ModelConfig) -> float:
    return max(cfg.min_half_spread * cfg.spread_multiplier, mid * pct)


def option_symbol(underlying: str, expiration: date, strike: float, kind: str = "put") -> str:
    letter = "C" if kind == "call" else "P"
    return f"{underlying}{expiration:%y%m%d}{letter}{round(strike * 1000):08d}"


def snapshot(
    s: SymbolSeries,
    i: int,
    sector: str | None,
    dte_min: int,
    dte_max: int,
    monthly_only: bool,
    lowest_width: float,
    cfg: ModelConfig,
    calls: bool = False,
) -> MarketSnapshot | None:
    """The put chain the screener would have seen at day i's close, with the calls from the
    money up to +30 % when `calls` (shares held: covered calls)."""
    atm = s.atm_iv[i]
    if atm is None or i < MIN_HISTORY_DAYS:
        return None
    h = s.history
    day = h.dates[i]
    spot = s.spot(i)
    skew = cfg.skew_etf if s.is_etf else cfg.skew_stock
    step = strike_step(spot, s.is_etf)
    quotes: list[OptionQuote] = []
    for exp in expirations(day, dte_min, dte_max, monthly_only):
        years = (exp - day).days / 365
        # Deep enough for a 0.05-delta short plus the widest long leg, up to just above spot.
        low = spot * math.exp(-3 * atm * 1.5 * math.sqrt(years)) - lowest_width
        k = math.floor(spot * 1.02 / step) * step
        while k > max(low, step / 2):
            mid = put_mid(spot, k, years, atm, skew, cfg.rate)
            if mid >= 0.01:
                hs = half_spread(mid, s.half_spread_pct, cfg)
                quotes.append(
                    OptionQuote(
                        symbol=option_symbol(h.symbol, exp, k),
                        option_type="put",
                        expiration=exp,
                        strike=round(k, 2),
                        bid=round(max(0.0, mid - hs), 4),
                        ask=round(mid + hs, 4),
                        iv=put_iv(spot, k, years, atm, skew),
                        open_interest=LIQUID,
                        volume=LIQUID,
                    )
                )
            k -= step
        if calls:
            k = math.ceil(spot / step) * step
            while k <= spot * 1.3:
                mid = call_mid(spot, k, years, atm, skew, cfg.rate)
                if mid >= 0.01:
                    hs = half_spread(mid, s.half_spread_pct, cfg)
                    quotes.append(
                        OptionQuote(
                            symbol=option_symbol(h.symbol, exp, k, "call"),
                            option_type="call",
                            expiration=exp,
                            strike=round(k, 2),
                            bid=round(max(0.0, mid - hs), 4),
                            ask=round(mid + hs, 4),
                            iv=put_iv(spot, k, years, atm, skew),
                            open_interest=LIQUID,
                            volume=LIQUID,
                        )
                    )
                k += step
    scale = h.split_factors[i]
    closes = [c * scale for c in h.closes[max(0, i - 260) : i + 1]]
    return MarketSnapshot(h.symbol, spot, closes, quotes, s.next_earnings(day), sector)
