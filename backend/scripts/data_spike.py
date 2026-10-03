"""Sprint 0 data spike: how many contracts survive each screener filter, per ticker.

Usage (needs network access to Yahoo Finance):
    python -m scripts.data_spike                # default universe (ETF, large caps, wheel)
    python -m scripts.data_spike AAPL MSFT F    # custom tickers

It prints a funnel per ticker (puts with 25-55 DTE -> OTM delta band -> liquidity -> spread
width -> earnings) and writes the surviving contracts to spike_results.csv. IV Rank is not
available from Yahoo: the script reports IV / 30-day realized volatility instead.

Standard library only (no yfinance/pandas/scipy), so it runs where PyPI is unreachable.
"""

import csv
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.marketdata.yahoo import YahooClient

ETFS = ["SPY", "QQQ", "IWM"]  # always screened: tradable during earnings season
LARGE_CAPS = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "JPM", "BAC", "XOM",
    "KO", "PFE", "T", "INTC",
]  # fmt: skip
# Wheel candidates: cash-secured puts need strike <= 20 (10 % of a 20k account = 2 000 collateral).
WHEEL = [
    "F", "SOFI", "AAL", "RIVN", "NCLH", "VALE", "ITUB", "HBAN", "KEY", "PCG",
    "CLF", "AGNC", "LYFT", "SNAP", "NIO", "RIG",
]  # fmt: skip
WHEEL_MAX_STRIKE = 20.0
DEFAULT_UNIVERSE = ETFS + LARGE_CAPS + WHEEL

RISK_FREE = 0.04


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass(frozen=True)
class Filters:
    dte_min: int = 25
    dte_max: int = 55
    delta_min: float = 0.15
    delta_max: float = 0.30
    min_open_interest: int = 100
    min_volume: int = 10
    max_spread_pct: float = 0.15


def put_delta(spot: float, strike: float, years: float, iv: float, r: float = RISK_FREE) -> float:
    """Black-Scholes put delta (negative). Good enough to bucket contracts for the spike."""
    if years <= 0 or iv <= 0:
        return -1.0 if strike > spot else 0.0
    d1 = (math.log(spot / strike) + (r + iv**2 / 2) * years) / (iv * math.sqrt(years))
    return norm_cdf(d1) - 1


def pop_short_put(spot: float, breakeven: float, years: float, iv: float) -> float:
    """Risk-neutral probability that the underlying ends above the breakeven at expiration."""
    if years <= 0 or iv <= 0:
        return 1.0 if spot > breakeven else 0.0
    d2 = (math.log(spot / breakeven) + (RISK_FREE - iv**2 / 2) * years) / (iv * math.sqrt(years))
    return norm_cdf(d2)


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


def _fetch(yahoo: YahooClient, ticker: str, today: date):  # pragma: no cover - network
    chart = yahoo.json(f"/v8/finance/chart/{ticker}", range="3mo", interval="1d")["chart"]
    closes = [c for c in chart["result"][0]["indicators"]["quote"][0]["close"] if c]
    spot = float(closes[-1])
    log_returns = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)][-30:]
    hv30 = statistics.stdev(log_returns) * math.sqrt(252)
    next_earnings = None
    try:
        summary = yahoo.json(f"/v10/finance/quoteSummary/{ticker}", modules="calendarEvents")
        dates = summary["quoteSummary"]["result"][0]["calendarEvents"]["earnings"]["earningsDate"]
        if dates:
            next_earnings = datetime.fromtimestamp(dates[0]["raw"], UTC).date()
    except Exception:
        pass
    chain = yahoo.json(f"/v7/finance/options/{ticker}")["optionChain"]["result"][0]
    puts: list[dict] = []
    for ts in chain["expirationDates"]:
        expiration = datetime.fromtimestamp(ts, UTC).date()
        if not 20 <= (expiration - today).days <= 60:
            continue
        data = yahoo.json(f"/v7/finance/options/{ticker}", date=ts)["optionChain"]["result"][0]
        for row in data["options"][0]["puts"]:
            puts.append(
                {
                    "ticker": ticker,
                    "symbol": row["contractSymbol"],
                    "expiration": expiration,
                    "strike": float(row["strike"]),
                    "bid": float(row.get("bid") or 0),
                    "ask": float(row.get("ask") or 0),
                    "iv": float(row.get("impliedVolatility") or 0),
                    "open_interest": int(row.get("openInterest") or 0),
                    "volume": int(row.get("volume") or 0),
                }
            )
    return spot, hv30, next_earnings, puts


def main(tickers: list[str]) -> None:  # pragma: no cover - network
    yahoo = YahooClient()
    today = date.today()
    filters = Filters()
    survivors: list[dict] = []
    stages = ["puts", "dte", "delta", "open_interest", "volume", "spread", "earnings"]
    print(f"{'ticker':<7}{'spot':>9}{'IV/HV':>7}  " + " ".join(f"{s:>13}" for s in stages))
    for ticker in tickers:
        try:
            spot, hv30, next_earnings, puts = _fetch(yahoo, ticker, today)
        except Exception as exc:
            print(f"{ticker:<7} erreur: {exc}")
            continue
        if ticker in WHEEL:
            puts = [p for p in puts if p["strike"] <= WHEEL_MAX_STRIKE]
        counts, rows = funnel(puts, spot, today, filters, next_earnings)
        atm_iv = min(puts, key=lambda p: abs(p["strike"] - spot))["iv"] if puts else 0
        ratio = atm_iv / hv30 if hv30 else 0
        line = " ".join(f"{counts[s]:>13}" for s in stages)
        print(f"{ticker:<7}{spot:>9.2f}{ratio:>7.2f}  {line}")
        survivors.extend(rows)
    with open("spike_results.csv", "w", newline="") as fh:
        if survivors:
            writer = csv.DictWriter(fh, fieldnames=list(survivors[0]))
            writer.writeheader()
            writer.writerows(survivors)
    print(f"\n{len(survivors)} contrats retenus -> spike_results.csv")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:] or DEFAULT_UNIVERSE)
