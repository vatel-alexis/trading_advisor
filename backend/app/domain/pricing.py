"""Black-Scholes helpers. Yahoo gives implied volatility but no greeks, so deltas are recomputed."""

import math
from typing import Literal

OptionType = Literal["put", "call"]


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _d1(spot: float, strike: float, years: float, iv: float, rate: float) -> float:
    return (math.log(spot / strike) + (rate + iv**2 / 2) * years) / (iv * math.sqrt(years))


def bs_delta(
    option_type: OptionType, spot: float, strike: float, years: float, iv: float, rate: float
) -> float:
    """Black-Scholes delta: negative for puts, positive for calls."""
    if years <= 0 or iv <= 0:
        itm = strike > spot if option_type == "put" else strike < spot
        return (-1.0 if option_type == "put" else 1.0) if itm else 0.0
    d1 = _d1(spot, strike, years, iv, rate)
    return norm_cdf(d1) - 1 if option_type == "put" else norm_cdf(d1)


def prob_above(spot: float, level: float, years: float, iv: float, rate: float) -> float:
    """Risk-neutral probability that the underlying ends above `level` at expiration."""
    if level <= 0:
        return 1.0
    if years <= 0 or iv <= 0:
        return 1.0 if spot > level else 0.0
    d2 = _d1(spot, level, years, iv, rate) - iv * math.sqrt(years)
    return norm_cdf(d2)


def spread_pct(bid: float, ask: float) -> float:
    """Bid/ask spread relative to the mid price."""
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else math.inf


def bs_put_price(spot: float, strike: float, years: float, iv: float, rate: float) -> float:
    """Black-Scholes price of a European put, no dividends."""
    if years <= 0 or iv <= 0:
        return max(0.0, strike - spot)
    d1 = _d1(spot, strike, years, iv, rate)
    d2 = d1 - iv * math.sqrt(years)
    return strike * math.exp(-rate * years) * norm_cdf(-d2) - spot * norm_cdf(-d1)


def bs_vega(spot: float, strike: float, years: float, iv: float, rate: float) -> float:
    """Price change per share for a 1 point (0.01) rise in implied volatility, puts and calls."""
    if years <= 0 or iv <= 0:
        return 0.0
    d1 = _d1(spot, strike, years, iv, rate)
    return spot * math.exp(-(d1**2) / 2) / math.sqrt(2 * math.pi) * math.sqrt(years) / 100


def bs_theta(
    option_type: OptionType, spot: float, strike: float, years: float, iv: float, rate: float
) -> float:
    """Price change per share over one calendar day (negative for a long option)."""
    if years <= 0 or iv <= 0:
        return 0.0
    d1 = _d1(spot, strike, years, iv, rate)
    d2 = d1 - iv * math.sqrt(years)
    pdf = math.exp(-(d1**2) / 2) / math.sqrt(2 * math.pi)
    decay = -spot * pdf * iv / (2 * math.sqrt(years))
    carry = rate * strike * math.exp(-rate * years)
    annual = decay + carry * norm_cdf(-d2) if option_type == "put" else decay - carry * norm_cdf(d2)
    return annual / 365
