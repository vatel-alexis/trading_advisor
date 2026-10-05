import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.backtest.data import MarketHistory, SymbolHistory, fill_earnings, split_factors
from app.backtest.engine import Trade, exit_fill, run_backtest
from app.backtest.report import buy_and_hold, cagr, max_drawdown, summarize
from app.backtest.synth import (
    ModelConfig,
    SymbolSeries,
    ex_earnings_hv,
    expirations,
    put_iv,
    snapshot,
    strike_step,
)
from app.domain.exits import PROFIT_TARGET, STOP_LOSS, TIME_EXIT
from app.domain.params import StrategyParams
from app.domain.pricing import bs_put_price

ETF_ONLY = replace(StrategyParams(), etfs=("SPY",), large_caps=(), wheel=())


def business_days(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def history(closes: list[float], start: date = date(2018, 1, 2), **kw) -> SymbolHistory:
    days = business_days(start, len(closes))
    return SymbolHistory("SPY", days, closes, [1.0] * len(closes), **kw)


def market(closes: list[float], vix: float = 0.2) -> MarketHistory:
    h = history(closes)
    return MarketHistory({"SPY": h}, {"^VIX": {d: vix for d in h.dates}})


def wavy(n: int, start: float = 300.0, drift: float = 0.0004) -> list[float]:
    """A gently rising price with a little noise, so realized volatility is not zero."""
    return [start * math.exp(drift * i + 0.01 * math.sin(i * 1.7)) for i in range(n)]


def test_split_factors_multiply_later_splits():
    days = [date(2024, 6, 6), date(2024, 6, 7), date(2024, 6, 10), date(2024, 6, 11)]
    factors = split_factors(days, [(date(2024, 6, 10), 10.0)])
    assert factors == [10.0, 10.0, 1.0, 1.0]


def test_fill_earnings_spreads_gaps_and_extends_quarterly():
    known = [date(2024, 1, 30), date(2024, 10, 30)]
    filled = fill_earnings(known, until=date(2025, 3, 1))
    # Two missing reports in the 274-day gap, then one per quarter after the last.
    assert filled[:4] == [
        date(2024, 1, 30),
        date(2024, 4, 30),
        date(2024, 7, 31),
        date(2024, 10, 30),
    ]
    assert filled[4] == date(2024, 10, 30) + timedelta(days=91)
    assert fill_earnings([], date(2025, 1, 1)) == []


def test_ex_earnings_hv_ignores_the_report_gap():
    closes = wavy(80)
    closes = closes[:60] + [c * 0.8 for c in closes[60:]]  # 20 % gap on day 60
    h = history(closes)
    with_report = replace(h, earnings=[h.dates[60]])
    assert ex_earnings_hv(h)[70] > 0.5  # the gap dominates
    assert ex_earnings_hv(with_report)[70] < 0.3


def test_expirations_fridays_in_window_and_monthlies():
    day = date(2026, 10, 2)
    weekly = expirations(day, 25, 55, monthly_only=False)
    assert all(e.weekday() == 4 and 25 <= (e - day).days <= 55 for e in weekly)
    assert weekly[0] == date(2026, 10, 30) and len(weekly) == 4
    assert expirations(day, 25, 55, monthly_only=True) == [date(2026, 11, 20)]


def test_strike_steps():
    assert strike_step(600, is_etf=True) == 1.0
    assert strike_step(12, is_etf=False) == 0.5
    assert strike_step(150, is_etf=False) == 2.5


def test_put_skew_raises_otm_iv():
    assert put_iv(100, 100, 0.1, 0.2, 0.25) == pytest.approx(0.2)
    assert put_iv(100, 90, 0.1, 0.2, 0.25) > 0.2
    assert bs_put_price(100, 90, 0, 0.2, 0.04) == 0.0
    assert bs_put_price(80, 90, 0, 0.2, 0.04) == pytest.approx(10.0)


def test_snapshot_builds_a_chain_the_screener_accepts():
    m = market(wavy(300))
    h = m.symbols["SPY"]
    s = SymbolSeries(h, [0.2] * len(h.dates), True, 0.008)
    snap = snapshot(s, 299, "ETF", 25, 55, False, 10.0, ModelConfig())
    assert snap is not None and snap.spot == pytest.approx(h.closes[299])
    assert all(q.option_type == "put" and q.bid < q.ask for q in snap.options)
    # Strikes reach below a 0.15-delta short minus the widest spread, and up to spot.
    strikes = [q.strike for q in snap.options]
    assert max(strikes) >= snap.spot * 0.99
    assert min(strikes) < snap.spot * 0.85


def _trade(credit: float, legs, expiration: date, entry: date) -> Trade:
    return Trade(
        underlying="SPY",
        strategy="put_credit_spread",
        group="etf",
        sector="ETF",
        entry_day=entry,
        expiration=expiration,
        quantity=1,
        legs=legs,
        credit=credit,
        collateral=500 - credit * 100,
        max_loss=500 - credit * 100,
        stress_loss=500 - credit * 100,
        short_delta=-0.2,
        pop=0.8,
        iv_rank=50.0,
        split_factor=1.0,
    )


def _series(opens, lows, highs, closes) -> SymbolSeries:
    h = history(closes, opens=opens, lows=lows, highs=highs)
    return SymbolSeries(h, [0.2] * len(closes), True, 0.008)


def test_stop_fills_at_the_stop_when_crossed_intraday():
    s = _series([100, 100], [100, 90], [100, 100], [100, 99])
    t = _trade(
        0.5, [(95.0, 1), (90.0, -1)], s.history.dates[0] + timedelta(days=45), date(2018, 1, 1)
    )
    reason, price = exit_fill(t, s, 1, StrategyParams(), ModelConfig())
    assert reason == STOP_LOSS
    # Stop at 2x the credit plus the natural half spreads, far below the mark at the low.
    assert 1.0 <= price < 1.1


def test_stop_after_a_gap_fills_at_the_open():
    s = _series([100, 91], [100, 90], [100, 92], [100, 91])
    t = _trade(
        0.5, [(95.0, 1), (90.0, -1)], s.history.dates[0] + timedelta(days=45), date(2018, 1, 1)
    )
    reason, price = exit_fill(t, s, 1, StrategyParams(), ModelConfig())
    assert reason == STOP_LOSS and price > 2.5


def test_profit_target_fills_at_its_limit():
    s = _series([100, 100], [100, 100], [100, 115], [100, 101])
    t = _trade(
        1.0, [(95.0, 1), (90.0, -1)], s.history.dates[0] + timedelta(days=45), date(2018, 1, 1)
    )
    assert exit_fill(t, s, 1, StrategyParams(), ModelConfig()) == (PROFIT_TARGET, 0.5)


def test_time_exit_at_21_dte_on_the_close():
    s = _series([100, 100], [99, 99], [101, 101], [100, 100])
    exp = s.history.dates[1] + timedelta(days=21)
    t = _trade(0.6, [(95.0, 1), (90.0, -1)], exp, date(2018, 1, 1))
    reason, price = exit_fill(t, s, 1, StrategyParams(), ModelConfig())
    assert reason == TIME_EXIT and 0 < price < 1.2


def test_backtest_in_a_calm_rising_market_takes_profits():
    m = market(wavy(500))
    days = m.symbols["SPY"].dates
    result = run_backtest(m, ETF_ONLY, days[300], days[-1])
    s = summarize(result)
    assert s.trades > 0
    assert not any(t.exit_reason == STOP_LOSS for t in result.trades)
    assert s.final > 20_000
    # Each spread's max loss stays within 1 % of the capital (the default risk budget).
    assert all(t.max_loss <= 20_000 * 1.2 * 0.01 for t in result.trades)


def test_backtest_in_a_crash_stops_out():
    closes = wavy(420) + [wavy(420)[-1] * (0.97**k) for k in range(1, 30)]
    m = market(closes, vix=0.35)
    days = m.symbols["SPY"].dates
    # Volatility rises with the crash, so the IV Rank filter lets entries through.
    vix = {d: 0.2 if i < 300 else 0.35 for i, d in enumerate(days)}
    m = MarketHistory(m.symbols, {"^VIX": vix})
    result = run_backtest(m, ETF_ONLY, days[300], days[-1])
    assert any(t.exit_reason == STOP_LOSS for t in result.trades)
    assert summarize(result).final < 20_000


def test_report_measures():
    assert max_drawdown([100, 120, 90, 130]) == pytest.approx(0.25)
    assert cagr(100, 121, 730) == pytest.approx(0.1, abs=1e-3)
    final, dd = buy_and_hold([(date(2020, 1, 1), 10.0), (date(2020, 1, 2), 5.0)], 1000)
    assert final == 500 and dd == 0.5
