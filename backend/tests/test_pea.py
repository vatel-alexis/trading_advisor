import json
from datetime import date, timedelta

import pytest

from app.domain import pea

TODAY = date(2026, 10, 6)


def business_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def monthly_index(**monthly_growth: float) -> tuple[list[date], dict[str, list[float]]]:
    """One point per month-end over 20 months; each equity grows at its monthly rate."""
    days = [date(2024 + (m // 12), m % 12 + 1, 28) for m in range(20)]
    index = {
        key: [100 * (1 + monthly_growth.get(key, 0.0)) ** i for i in range(len(days))]
        for key in pea.ASSET_BY_KEY
    }
    index[pea.CASH] = [100 * 1.002**i for i in range(len(days))]
    return days, index


def latest_target(level_key: str, days, index) -> dict[str, float]:
    return pea.target(pea.LEVEL_BY_KEY[level_key], pea.scores(index, list(range(len(days)))))


def test_rotation_keeps_the_best_rising_etfs() -> None:
    days, index = monthly_index(nasdaq=0.03, emerging=0.02, sp500=0.01, japan=0.005, europe=-0.01)

    assert latest_target("moyen", days, index) == pytest.approx(
        {"nasdaq": 1 / 3, "emerging": 1 / 3, "sp500": 1 / 3}
    )
    assert latest_target("dynamique", days, index) == pytest.approx(
        {"nasdaq": 0.5, "emerging": 0.5}
    )
    assert latest_target("securite", days, index) == pytest.approx(
        {"nasdaq": 0.2, "emerging": 0.2, "sp500": 0.2, "cash": 0.4}
    )


def test_a_falling_etf_is_replaced_by_the_money_market() -> None:
    # Only two ETFs rise: the third slot of the rotation goes to the money market.
    days, index = monthly_index(nasdaq=0.03, sp500=0.01, emerging=-0.01, japan=-0.02, europe=-0.03)
    assert latest_target("moyen", days, index) == pytest.approx(
        {"nasdaq": 1 / 3, "sp500": 1 / 3, "cash": 1 / 3}
    )
    # Everything falls: all in the money market.
    days, index = monthly_index(
        nasdaq=-0.01, sp500=-0.01, emerging=-0.01, japan=-0.02, europe=-0.03
    )
    assert latest_target("dynamique", days, index) == {"cash": 1.0}


def test_a_rising_score_below_its_average_stays_out() -> None:
    days, index = monthly_index(nasdaq=0.03, sp500=0.02, emerging=0.01)
    # Nasdaq fell hard over the last month: still up over 12 months, but under its 10-month
    # average, so its slot goes to the money market.
    index["nasdaq"][-1] = index["nasdaq"][-2] * 0.7
    score = {s.key: s for s in pea.scores(index, list(range(len(days))))}["nasdaq"]
    assert not score.trend_ok
    assert "nasdaq" not in latest_target("moyen", days, index)


def test_too_short_a_history_gives_no_score() -> None:
    days, index = monthly_index(nasdaq=0.03)
    ends = list(range(10))
    assert all(s.momentum is None for s in pea.scores(index, ends))
    assert pea.target(pea.LEVEL_BY_KEY["moyen"], pea.scores(index, ends)) == {"cash": 1.0}


def test_proxies_in_euros_extend_the_pea_etfs() -> None:
    days = business_days(date(2016, 9, 1), date(2016, 11, 30))
    # The proxy is flat in dollars while the euro rises 10 %: -9.1 % in euros. From its start
    # (2016-11-01), the PEA ETF's own prices take over.
    fx = {d: (1.0 if d < date(2016, 10, 1) else 1.1) for d in days}
    prices = {
        "SPY": {d: 200.0 for d in days},
        "PSP5.PA": {d: 10.0 * (1.01 if d >= date(2016, 11, 15) else 1) for d in days},
    }
    out_days, index = pea.euro_indexes(prices, fx)
    sp = dict(zip(out_days, index["sp500"], strict=True))

    assert sp[date(2016, 9, 30)] == pytest.approx(100.0)
    assert sp[date(2016, 10, 31)] == pytest.approx(100 / 1.1)
    assert sp[date(2016, 11, 30)] == pytest.approx(100 / 1.1 * 1.01)
    # The money market accrues the euro short rate table before its own ETF.
    cash = dict(zip(out_days, index["cash"], strict=True))
    assert cash[date(2016, 11, 30)] < 100.0  # negative rates in 2016


def synthetic_history() -> tuple[list[date], dict[str, list[float]]]:
    """2005-2026 business days: the Nasdaq rises steadily, the others fall, then the Nasdaq
    falls and Japan rises from 2020."""
    days = business_days(date(2005, 1, 3), TODAY)
    index: dict[str, list[float]] = {k: [] for k in pea.ASSET_BY_KEY}
    values = dict.fromkeys(pea.ASSET_BY_KEY, 100.0)
    for d in days:
        late = d.year >= 2020
        daily = {
            "nasdaq": -0.0008 if late else 0.0006,
            "japan": 0.0008 if late else -0.0003,
            "sp500": -0.0002,
            "europe": -0.0003,
            "emerging": -0.0004,
            pea.CASH: 0.00005,
        }
        for k in values:
            values[k] *= 1 + daily[k]
            index[k].append(values[k])
    return days, index


def test_backtest_trades_the_day_after_the_signal_and_counts_adjustments() -> None:
    days, index = synthetic_history()
    bt = pea.backtest(pea.LEVEL_BY_KEY["dynamique"], days, index)

    first = bt.adjustments[0]
    assert first.signal_day == date(2006, 12, 29) and first.day == date(2007, 1, 2)
    assert first.after == pytest.approx({"nasdaq": 0.5, "cash": 0.5})
    signals = [a for a in bt.adjustments[1:] if a.reason == "signal"]
    # The Nasdaq leaves, then Japan comes in: only a few real changes over 20 years.
    assert 1 <= len(signals) <= 6
    assert bt.adjustments[-1].after.get("japan") == pytest.approx(0.5)
    assert all(a.day > a.signal_day for a in bt.adjustments)
    stats = pea.summary(bt)
    assert stats["adjustments"] == len(bt.adjustments) - 1
    assert stats["periods"]["15"] is not None and stats["max_drawdown"] > 0


def test_costs_lower_the_result() -> None:
    days, index = synthetic_history()
    level = pea.LEVEL_BY_KEY["moyen"]
    with_costs = pea.backtest(level, days, index).values[-1]
    saved = pea.COST
    try:
        pea.COST = 0.0
        free = pea.backtest(level, days, index).values[-1]
    finally:
        pea.COST = saved
    assert free > with_costs


def test_report_gives_targets_changes_and_a_provisional_signal() -> None:
    days, index = synthetic_history()
    report = pea.report(days, index, TODAY)

    # October is in progress: the targets come from the end of September.
    assert report["signal_day"] == "2026-09-30"
    assert report["provisional_day"] == TODAY.isoformat()
    by_key = {lv["key"]: lv for lv in report["levels"]}
    assert set(by_key) == {"securite", "moyen", "dynamique"}
    dyn = by_key["dynamique"]
    assert dyn["target"] == pytest.approx({"japan": 0.5, "cash": 0.5})
    assert dyn["changes"] == [] and dyn["preview_changes"] == []
    assert sum(by_key["securite"]["target"].values()) == pytest.approx(1.0)
    assert set(dyn["summary"]["periods"]) == {"1", "3", "5", "10", "15"}
    assert report["benchmark"]["adjustments"] == 0
    json.dumps(report)  # stored as JSON


def test_changes_list_buys_and_sells() -> None:
    assert pea.changes({"nasdaq": 0.5, "cash": 0.5}, {"japan": 0.5, "cash": 0.5}) == [
        {"asset": "nasdaq", "from": 0.5, "to": 0.0, "action": "vendre"},
        {"asset": "japan", "from": 0.0, "to": 0.5, "action": "acheter"},
    ]
