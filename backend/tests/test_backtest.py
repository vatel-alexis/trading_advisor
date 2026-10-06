import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.backtest.data import MarketHistory, SymbolHistory, fill_earnings, split_factors
from app.backtest.engine import (
    ASSIGNMENT,
    CALLED_AWAY,
    SHARES,
    BacktestResult,
    ExitContext,
    Trade,
    exit_fill,
    run_backtest,
)
from app.backtest.report import (
    buy_and_hold,
    cagr,
    data_quality,
    max_consecutive_losses,
    max_drawdown,
    profit_factor,
    recovery,
    robustness,
    rolling_windows,
    split_periods,
    stress_tests,
    summarize,
    wheel_summary,
)
from app.backtest.synth import (
    EXECUTION_SCENARIOS,
    REALISTIC,
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


# --- headline measures ---------------------------------------------------------------------


def test_drawdown_is_measured_in_time_order() -> None:
    # The 50 trough comes after the 200 peak: 75 %; a fall before the peak does not count.
    assert max_drawdown([100, 60, 200, 50, 300]) == pytest.approx(0.75)
    assert max_drawdown([100, 110, 120]) == 0.0


def test_profit_factor_and_losing_streak() -> None:
    pnls = [100, -50, -50, 30, -20, -10, -5, 60]
    assert profit_factor(pnls) == pytest.approx(190 / 135)
    assert profit_factor([10, 20]) == math.inf
    assert max_consecutive_losses(pnls) == 3


def test_recovery_time_and_an_unrecovered_end() -> None:
    d = date(2020, 1, 1)
    points = [(d, 100.0), (d + timedelta(days=10), 90.0), (d + timedelta(days=40), 101.0)]
    assert recovery(points) == (40, True)
    under = [*points, (d + timedelta(days=50), 95.0), (d + timedelta(days=200), 96.0)]
    assert recovery(under) == (160, False)


def _result(values: list[float], start: date = date(2020, 1, 1)) -> BacktestResult:
    days = [start + timedelta(days=i) for i in range(len(values))]
    return BacktestResult(StrategyParams(), ModelConfig(), 100.0, [], [
        (d, v, 0.0) for d, v in zip(days, values, strict=True)
    ])  # fmt: skip


def test_rolling_windows_and_robustness() -> None:
    rising = _result([100 + i * 0.05 for i in range(800)])
    windows = rolling_windows(rising)
    assert len(windows) >= 5 and all(w["return"] > 0 for w in windows)
    verdict = robustness(split_periods(rising), windows)
    assert verdict["label"] == "robuste" and verdict["positive_windows"] == 1.0

    falling = _result([100 - i * 0.05 for i in range(800)])
    assert robustness(split_periods(falling), rolling_windows(falling))["label"] == "fragile"


def test_calibration_and_out_of_sample_split() -> None:
    result = _result([100 + i for i in range(100)])
    periods = split_periods(result)
    assert periods["calibration"]["end"] < periods["out_of_sample"]["start"]
    # The out-of-sample return starts from the value at the split, not the capital.
    assert periods["out_of_sample"]["net_return"] == pytest.approx(199 / 159 - 1, abs=1e-4)


def test_stress_tests_cost_more_than_the_reference() -> None:
    entry = date(2020, 1, 1)
    trades = []
    for k in range(20):
        t = _trade(1.0, [(95.0, 1), (90.0, -1)], entry + timedelta(days=60), entry)
        t.entry_spread, t.exit_spread = 0.05, 0.05 if k % 4 == 0 else 0.0
        t.exit_day = entry + timedelta(days=10 + k)
        t.exit_reason, t.exit_price = ("stop_loss", 2.1) if k % 4 == 0 else ("profit_target", 0.5)
        trades.append(t)
    equity = [(entry + timedelta(days=i), 20_000.0, 0.0) for i in range(40)]
    risk = [(entry + timedelta(days=i), 2_000.0, 1_500.0) for i in range(40)]
    result = BacktestResult(StrategyParams(), ModelConfig(), 20_000.0, trades, equity,
                            open_risk=risk)  # fmt: skip
    rows = {row["key"]: row for row in stress_tests(result)}
    base = rows["base"]["net_return"]
    for key in ("slippage", "spreads", "win_rate_5", "win_rate_10"):
        assert rows[key]["net_return"] < base, key
    assert rows["win_rate_10"]["win_rate"] < rows["base"]["win_rate"]
    # All open positions losing their stress loss at once: 1 500 on 20 000.
    assert rows["correlated"]["loss_pct"] == pytest.approx(0.075)


def test_data_quality_never_claims_historical_quotes() -> None:
    params = replace(StrategyParams(), use_volume_filter=True, use_open_interest_filter=True)
    entry = date(2020, 1, 1)
    trade = _trade(1.0, [(95.0, 1), (90.0, -1)], entry + timedelta(days=60), entry)
    result = BacktestResult(params, ModelConfig(), 20_000.0, [trade], [(entry, 20_000.0, 0.0)])
    quality = data_quality(result)
    assert quality["prices"] == "reconstitués" and quality["level"] == "moyenne"
    assert any("volume" in f for f in quality["untested_filters"])
    assert any("open interest" in f for f in quality["untested_filters"])
    other = replace(trade, underlying="AAPL")
    assert data_quality(replace(result, trades=[other]))["level"] == "faible"


# --- engine: loss limits, stop signals, execution scenarios, the whole wheel ---------------


def test_the_delta_signal_stops_on_the_close() -> None:
    # Close at 96: the 95 put is close to the money; no stop on the cost (low = close).
    s = _series([100, 96], [100, 96], [100, 100], [100, 96])
    t = _trade(
        1.2, [(95.0, 1), (90.0, -1)], s.history.dates[0] + timedelta(days=45), date(2018, 1, 1)
    )
    params = replace(StrategyParams(), stop_delta=0.40)
    assert exit_fill(t, s, 1, params, ModelConfig()) is None  # no monitor view: cost only
    reason, _ = exit_fill(t, s, 1, params, ModelConfig(), ExitContext())
    assert reason == STOP_LOSS and t.triggers[0] in ("delta", "breach")


def test_execution_scenarios_rank_from_optimistic_to_pessimistic() -> None:
    m = market(wavy(500))
    days = m.symbols["SPY"].dates
    finals = {}
    for key, (_, overrides) in EXECUTION_SCENARIOS.items():
        result = run_backtest(
            m, ETF_ONLY, days[300], days[-1], model=replace(ModelConfig(), **overrides)
        )
        finals[key] = result.equity[-1][1]
    assert finals["optimiste"] >= finals["realiste"] >= finals["pessimiste"]
    assert ModelConfig().entry_slippage == EXECUTION_SCENARIOS[REALISTIC][1]["entry_slippage"]


def test_the_loss_limits_block_new_entries() -> None:
    closes = wavy(420) + [wavy(420)[-1] * (0.97**k) for k in range(1, 30)] + wavy(60, 250)
    m = market(closes, vix=0.35)
    days = m.symbols["SPY"].dates
    vix = {d: 0.2 if i < 300 else 0.35 for i, d in enumerate(days)}
    m = MarketHistory(m.symbols, {"^VIX": vix})
    tight = replace(ETF_ONLY, max_drawdown_pct=0.001, max_monthly_loss_pct=0.001)
    result = run_backtest(m, tight, days[300], days[-1])
    assert result.blocked_days > 0


def _wheel_market() -> MarketHistory:
    """A $6 stock that falls 30 % after the first put, stays down, then recovers."""
    n = 700
    days = business_days(date(2018, 1, 2), n)

    def price(i: int) -> float:
        if i < 330:
            v = 6.0
        elif i < 350:
            v = 6.0 * (1 - 0.3 * (i - 330) / 20)
        elif i < 450:
            v = 4.2
        else:
            v = 4.2 * (1 + 0.6 * min(1, (i - 450) / 40))
        return v * math.exp(0.02 * math.sin(i * 1.3))

    spy = [300 * math.exp(0.0003 * i + 0.01 * math.sin(i * 1.7)) for i in range(n)]
    stock = [price(i) for i in range(n)]
    return MarketHistory(
        {
            "SPY": SymbolHistory("SPY", days, spy, [1.0] * n),
            "SOFI": SymbolHistory("SOFI", days, stock, [1.0] * n, sector="Financials"),
        },
        {"^VIX": {d: 0.22 for d in days}},
    )


WHEEL = replace(
    StrategyParams(),
    enable_etfs=False,
    large_caps=(),
    wheel=(),
    true_wheel=("SOFI",),
    use_earnings_filter=False,
    use_iv_rank_filter=False,
    use_take_profit=False,
)


def test_the_whole_wheel_is_simulated() -> None:
    m = _wheel_market()
    days = m.symbols["SPY"].dates
    result = run_backtest(m, WHEEL, days[300], days[-1])

    puts = [t for t in result.trades if t.strategy == "cash_secured_put"]
    lots = [t for t in result.trades if t.strategy == SHARES]
    calls = [t for t in result.trades if t.strategy == "covered_call"]
    assert puts[0].exit_reason == ASSIGNMENT and len(lots) == 1
    assert lots[0].entry_day == puts[0].exit_day and lots[0].quantity == puts[0].quantity
    assert calls and all(c.entry_day >= lots[0].entry_day for c in calls)
    # The account value counts puts, calls and the shares marked at the close.
    total = sum(t.pnl for t in result.trades)
    assert result.equity[-1][1] == pytest.approx(20_000 + total)
    wheel = wheel_summary(result)
    assert wheel["assignments"] == 1 and wheel["complete"]
    assert wheel["total_pnl"] == pytest.approx(
        wheel["puts_pnl"] + wheel["calls_pnl"] + wheel["shares_pnl"], abs=0.02
    )


def test_a_covered_call_is_never_sold_without_shares() -> None:
    m = _wheel_market()
    days = m.symbols["SPY"].dates
    result = run_backtest(m, WHEEL, days[300], days[-1])
    lots = [t for t in result.trades if t.strategy == SHARES]
    calls = sorted(
        (t for t in result.trades if t.strategy == "covered_call"), key=lambda t: t.entry_day
    )
    for call in calls:
        held = [
            lot
            for lot in lots
            if lot.entry_day <= call.entry_day
            and (lot.exit_day is None or lot.exit_day >= (call.exit_day or call.entry_day))
        ]
        assert held and call.quantity * 100 <= sum(lot.shares_adj for lot in held)
    # One call at a time on the lot: never two open together.
    for a, b in zip(calls, calls[1:], strict=False):
        assert a.exit_day is not None and a.exit_day <= b.entry_day


def test_a_called_away_lot_closes_the_wheel() -> None:
    # Expiration day, close at 12: a call at 11 takes the shares, a put at 13 gives them.
    s = _series([10, 12], [10, 12], [10, 12], [10, 12])
    exp = s.history.dates[1]
    params = replace(StrategyParams(), use_take_profit=False)
    call = _trade(0.3, [(11.0, 1)], exp, date(2018, 1, 1))
    call.strategy, call.option_type, call.collateral = "covered_call", "call", 0.0
    assert exit_fill(call, s, 1, params, ModelConfig()) == (CALLED_AWAY, 1.0)
    put = _trade(0.3, [(13.0, 1)], exp, date(2018, 1, 1))
    put.strategy = "cash_secured_put"
    assert exit_fill(put, s, 1, params, ModelConfig()) == (ASSIGNMENT, 1.0)
