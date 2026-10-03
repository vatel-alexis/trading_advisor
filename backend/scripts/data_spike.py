"""Sprint 0 data spike: how many contracts survive each screener filter, per ticker.

Usage (needs network access to Yahoo Finance):
    python -m scripts.data_spike                # default 20-ticker universe
    python -m scripts.data_spike AAPL MSFT F    # custom tickers

It prints a funnel per ticker (puts with 30-50 DTE -> OTM delta band -> liquidity -> spread
width -> earnings) and writes the surviving contracts to spike_results.csv. IV Rank is not
available from Yahoo: the script reports IV / 30-day realized volatility instead.
"""

import math
import sys
from dataclasses import dataclass
from datetime import date

from scipy.stats import norm

DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD",
    "JPM", "BAC", "XOM", "KO", "PFE", "F", "T", "INTC", "SOFI", "AAL",
]  # fmt: skip

RISK_FREE = 0.04


@dataclass(frozen=True)
class Filters:
    dte_min: int = 30
    dte_max: int = 50
    delta_min: float = 0.15
    delta_max: float = 0.30
    min_open_interest: int = 500
    min_volume: int = 100
    max_spread_pct: float = 0.10


def put_delta(spot: float, strike: float, years: float, iv: float, r: float = RISK_FREE) -> float:
    """Black-Scholes put delta (negative). Good enough to bucket contracts for the spike."""
    if years <= 0 or iv <= 0:
        return -1.0 if strike > spot else 0.0
    d1 = (math.log(spot / strike) + (r + iv**2 / 2) * years) / (iv * math.sqrt(years))
    return norm.cdf(d1) - 1


def pop_short_put(spot: float, breakeven: float, years: float, iv: float) -> float:
    """Risk-neutral probability that the underlying ends above the breakeven at expiration."""
    if years <= 0 or iv <= 0:
        return 1.0 if spot > breakeven else 0.0
    d2 = (math.log(spot / breakeven) + (RISK_FREE - iv**2 / 2) * years) / (iv * math.sqrt(years))
    return float(norm.cdf(d2))


def spread_pct(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else math.inf


def funnel(puts: list[dict], spot: float, today: date, f: Filters, next_earnings: date | None):
    """Apply the filters in order; return the count after each stage and the survivors."""
    counts: dict[str, int] = {"puts": len(puts)}
    rows = [p for p in puts if f.dte_min <= (p["expiration"] - today).days <= f.dte_max]
    counts["dte"] = len(rows)
    for p in rows:
        years = (p["expiration"] - today).days / 365
        p["delta"] = put_delta(spot, p["strike"], years, p["iv"])
    rows = [p for p in rows if f.delta_min <= abs(p["delta"]) <= f.delta_max]
    counts["delta"] = len(rows)
    rows = [p for p in rows if p["open_interest"] >= f.min_open_interest]
    counts["open_interest"] = len(rows)
    rows = [p for p in rows if p["volume"] >= f.min_volume]
    counts["volume"] = len(rows)
    rows = [p for p in rows if spread_pct(p["bid"], p["ask"]) <= f.max_spread_pct]
    counts["spread"] = len(rows)
    if next_earnings is not None:
        rows = [p for p in rows if p["expiration"] < next_earnings or next_earnings < today]
    counts["earnings"] = len(rows)
    for p in rows:
        years = (p["expiration"] - today).days / 365
        mid = (p["bid"] + p["ask"]) / 2
        p["pop"] = pop_short_put(spot, p["strike"] - mid, years, p["iv"])
        p["aroc"] = mid / (p["strike"] - mid) * 365 / (p["expiration"] - today).days
    return counts, rows


def _fetch(ticker: str, today: date):  # pragma: no cover - network
    import yfinance as yf

    t = yf.Ticker(ticker)
    hist = t.history(period="3mo")["Close"]
    spot = float(hist.iloc[-1])
    log_returns = (hist / hist.shift(1)).apply(math.log).dropna().tail(30)
    hv30 = float(log_returns.std() * math.sqrt(252))
    try:
        cal = t.calendar or {}
        earnings = cal.get("Earnings Date") or []
        next_earnings = earnings[0] if earnings else None
    except Exception:
        next_earnings = None
    puts: list[dict] = []
    for exp in t.options:
        expiration = date.fromisoformat(exp)
        if not 25 <= (expiration - today).days <= 55:
            continue
        for row in t.option_chain(exp).puts.itertuples():
            puts.append(
                {
                    "ticker": ticker,
                    "symbol": row.contractSymbol,
                    "expiration": expiration,
                    "strike": float(row.strike),
                    "bid": float(row.bid or 0),
                    "ask": float(row.ask or 0),
                    "iv": float(row.impliedVolatility or 0),
                    "open_interest": int(0 if math.isnan(row.openInterest) else row.openInterest),
                    "volume": int(0 if math.isnan(row.volume) else row.volume),
                }
            )
    return spot, hv30, next_earnings, puts


def main(tickers: list[str]) -> None:  # pragma: no cover - network
    import pandas as pd

    today = date.today()
    filters = Filters()
    survivors: list[dict] = []
    stages = ["puts", "dte", "delta", "open_interest", "volume", "spread", "earnings"]
    print(f"{'ticker':<7}{'spot':>9}{'IV/HV':>7}  " + " ".join(f"{s:>13}" for s in stages))
    for ticker in tickers:
        try:
            spot, hv30, next_earnings, puts = _fetch(ticker, today)
        except Exception as exc:
            print(f"{ticker:<7} erreur: {exc}")
            continue
        counts, rows = funnel(puts, spot, today, filters, next_earnings)
        atm_iv = min(puts, key=lambda p: abs(p["strike"] - spot))["iv"] if puts else 0
        ratio = atm_iv / hv30 if hv30 else 0
        line = " ".join(f"{counts[s]:>13}" for s in stages)
        print(f"{ticker:<7}{spot:>9.2f}{ratio:>7.2f}  {line}")
        survivors.extend(rows)
    pd.DataFrame(survivors).to_csv("spike_results.csv", index=False)
    print(f"\n{len(survivors)} contrats retenus -> spike_results.csv")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:] or DEFAULT_UNIVERSE)
