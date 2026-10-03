from datetime import date, timedelta

import pytest

from scripts.data_spike import Filters, funnel, pop_short_put, put_delta, spread_pct

TODAY = date(2026, 10, 5)


def make_put(strike: float, dte: int, **overrides) -> dict:
    put = {
        "expiration": TODAY + timedelta(days=dte),
        "strike": strike,
        "bid": 1.00,
        "ask": 1.05,
        "iv": 0.30,
        "open_interest": 1000,
        "volume": 200,
    }
    put.update(overrides)
    return put


def test_otm_put_delta_is_between_zero_and_minus_half() -> None:
    delta = put_delta(spot=100, strike=93, years=40 / 365, iv=0.30)
    assert -0.5 < delta < 0


def test_pop_exceeds_one_minus_delta_because_premium_lowers_breakeven() -> None:
    years, iv = 40 / 365, 0.30
    delta = put_delta(100, 93, years, iv)
    assert pop_short_put(100, 93 - 1.0, years, iv) > 1 + delta


def test_spread_pct_is_relative_to_mid() -> None:
    assert spread_pct(0.95, 1.05) == pytest.approx(0.10)
    assert spread_pct(0, 0) == float("inf")


def test_funnel_drops_contracts_at_each_stage() -> None:
    puts = [
        make_put(93, 40),  # survives
        make_put(93, 10),  # DTE too short
        make_put(99, 40),  # delta too high
        make_put(93, 40, open_interest=10),
        make_put(93, 40, volume=5),
        make_put(93, 40, bid=0.50, ask=1.50),  # spread too wide
    ]
    counts, rows = funnel(puts, spot=100, today=TODAY, f=Filters(), next_earnings=None)

    assert counts == {
        "puts": 6,
        "dte": 5,
        "delta": 4,
        "open_interest": 3,
        "volume": 2,
        "spread": 1,
        "earnings": 1,
    }
    assert rows[0]["pop"] > 0.7 and rows[0]["aroc"] > 0


def test_funnel_rejects_expirations_after_upcoming_earnings() -> None:
    puts = [make_put(93, 40)]
    earnings = TODAY + timedelta(days=20)

    counts, _ = funnel(puts, spot=100, today=TODAY, f=Filters(), next_earnings=earnings)

    assert counts["earnings"] == 0
