"""Synthetic option chains priced with Black-Scholes, for offline engine tests."""

import math
from datetime import date, timedelta

from app.domain.market import MarketSnapshot, OptionQuote
from app.domain.pricing import norm_cdf

TODAY = date(2026, 10, 5)
RATE = 0.04


def bs_price(option_type: str, spot: float, strike: float, years: float, iv: float) -> float:
    d1 = (math.log(spot / strike) + (RATE + iv**2 / 2) * years) / (iv * math.sqrt(years))
    d2 = d1 - iv * math.sqrt(years)
    discount = strike * math.exp(-RATE * years)
    if option_type == "put":
        return discount * norm_cdf(-d2) - spot * norm_cdf(-d1)
    return spot * norm_cdf(d1) - discount * norm_cdf(d2)


def closes_with_vol_range(spot: float, low: float = 0.10, high: float = 0.40) -> list[float]:
    """One year of closes whose 30-day realized vol drifts from `low` to `high` and back."""
    closes = [spot]
    for i in range(252):
        vol = low + (high - low) * (1 - abs(i - 126) / 126)
        step = vol / math.sqrt(252) * (1 if i % 2 == 0 else -1)
        closes.append(closes[-1] * math.exp(step))
    return [c * spot / closes[-1] for c in closes]


def make_chain(
    symbol: str,
    spot: float,
    strikes: list[float],
    iv: float = 0.30,
    dtes: tuple[int, ...] = (30, 45),
    option_types: tuple[str, ...] = ("put",),
    open_interest: int = 1000,
    volume: int = 200,
    spread: float = 0.04,
    today: date = TODAY,
) -> list[OptionQuote]:
    quotes = []
    for dte in dtes:
        expiration = today + timedelta(days=dte)
        for option_type in option_types:
            for strike in strikes:
                mid = bs_price(option_type, spot, strike, dte / 365, iv)
                if mid < 0.02:
                    continue
                half = mid * spread / 2
                quotes.append(
                    OptionQuote(
                        symbol=f"{symbol}{expiration:%y%m%d}{option_type[0].upper()}{strike:g}",
                        option_type=option_type,  # type: ignore[arg-type]
                        expiration=expiration,
                        strike=strike,
                        bid=round(mid - half, 4),
                        ask=round(mid + half, 4),
                        iv=iv,
                        open_interest=open_interest,
                        volume=volume,
                    )
                )
    return quotes


def make_snapshot(
    symbol: str,
    spot: float,
    strike_step: float,
    iv: float = 0.30,
    next_earnings: date | None = None,
    sector: str | None = "Technology",
    **chain_kwargs,
) -> MarketSnapshot:
    base = round(spot * 0.70 / strike_step) * strike_step
    strikes = [round(base + i * strike_step, 2) for i in range(int(spot * 0.6 / strike_step))]
    return MarketSnapshot(
        symbol=symbol,
        spot=spot,
        closes=closes_with_vol_range(spot),
        options=make_chain(symbol, spot, strikes, iv=iv, **chain_kwargs),
        next_earnings=next_earnings,
        sector=sector,
    )
