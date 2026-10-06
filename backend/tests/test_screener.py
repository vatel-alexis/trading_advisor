from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.market import PutCallRatio, put_call_ratio
from app.domain.params import StrategyParams
from app.domain.risk import AccountState
from app.domain.screener import (
    CASH_SECURED_PUT,
    COVERED_CALL,
    PUT_CREDIT_SPREAD,
    SHORT_PUT,
    ShareLot,
    screen,
)
from tests.chains import TODAY, make_snapshot

PARAMS = StrategyParams()
# Large caps are off by default; the tests that use them switch them on.
LARGE = replace(PARAMS, enable_large_caps=True)
ACCOUNT = AccountState(20_000)
LATE_EARNINGS = TODAY + timedelta(days=90)


def test_etf_gets_a_sized_put_credit_spread() -> None:
    result = screen([make_snapshot("SPY", 500, 1, sector=None)], TODAY, PARAMS, ACCOUNT)

    assert [c.underlying for c in result.selected] == ["SPY"]
    deal = result.selected[0]
    short, long = deal.legs
    width = short.quote.strike - long.quote.strike
    assert deal.strategy == PUT_CREDIT_SPREAD and deal.sector == "ETF"
    assert short.side == "sell" and long.side == "buy"
    # 5 wide does not fit the 200 $ budget (1 % of 20 000): the next width that does.
    assert width in PARAMS.spread_widths and width < 5
    assert PARAMS.delta_min <= abs(deal.short_delta) <= PARAMS.delta_max
    assert deal.credit >= PARAMS.min_credit
    assert deal.max_loss == pytest.approx((width - deal.credit) * 100) == deal.collateral
    # Contracts = floor(risk budget / max loss of one spread).
    assert deal.quantity == int(200 // deal.max_loss) >= 1
    assert deal.sizing.binding == "risk_budget" and deal.sizing.risk_budget == 200
    assert 0.6 < deal.pop < 0.95 and deal.aroc >= PARAMS.min_aroc
    assert deal.iv_rank is not None and deal.iv_rank.method == "hv_proxy"
    assert result.funnel["selected"] == 1


def test_earnings_before_expiration_rejects_a_stock() -> None:
    soon = TODAY + timedelta(days=20)
    snaps = [
        make_snapshot("AAPL", 300, 5, next_earnings=soon),
        make_snapshot("MSFT", 400, 5, next_earnings=None),  # unknown date: rejected
        make_snapshot("JPM", 300, 5, next_earnings=LATE_EARNINGS),
    ]
    result = screen(snaps, TODAY, LARGE, ACCOUNT)

    assert [c.underlying for c in result.ranked] == ["JPM"]


def test_low_iv_rank_rejects_the_underlying() -> None:
    # IV below the whole one-year realized volatility range: rank 0.
    snap = make_snapshot("QQQ", 400, 5, iv=0.08)
    result = screen([snap], TODAY, PARAMS, ACCOUNT)

    assert result.ranked == []
    assert result.funnel["iv_rank"] == 0 < result.funnel["contracts"]


def test_short_put_income_is_sized_on_its_stress_loss() -> None:
    snap = make_snapshot("SOFI", 8, 0.5, iv=0.60, next_earnings=LATE_EARNINGS)
    result = screen([snap], TODAY, PARAMS, ACCOUNT)

    deal = result.selected[0]
    strike = deal.legs[0].quote.strike
    # Not called a wheel: the put is bought back, at 21 DTE at the latest.
    assert deal.strategy == SHORT_PUT and len(deal.legs) == 1
    # Three separate amounts: cash held, stock to zero, a 30 % gap down.
    assert deal.collateral == strike * 100
    assert deal.max_loss == pytest.approx((strike - deal.credit) * 100)
    assert deal.stress_loss == pytest.approx((strike - 8 * 0.7 - deal.credit) * 100)
    assert deal.stress_loss < deal.max_loss < deal.collateral
    assert deal.quantity == int(200 // deal.stress_loss) >= 1


def test_a_wheel_put_whose_stress_loss_exceeds_the_budget_is_no_trade() -> None:
    snap = make_snapshot("F", 19, 0.5, iv=0.50, next_earnings=LATE_EARNINGS)
    result = screen([snap], TODAY, PARAMS, ACCOUNT)

    assert result.selected == [] and result.skipped == {"F": "risk_budget"}
    (deal,) = result.ranked
    assert deal.sizing.quantity == 0 and deal.sizing.risk_per_contract > 200


def test_sector_limit_keeps_two_etfs() -> None:
    etfs = [("SPY", 500), ("QQQ", 400), ("IWM", 200)]
    snaps = [make_snapshot(s, p, 1, sector=None) for s, p in etfs]
    result = screen(snaps, TODAY, PARAMS, ACCOUNT)

    assert len(result.selected) == 2
    assert list(result.skipped.values()) == ["sector_limit"]


def test_max_deals_and_engaged_capital() -> None:
    names = [("AAPL", 300), ("MSFT", 400), ("JPM", 250), ("XOM", 150), ("KO", 100), ("T", 50)]
    snaps = [make_snapshot(s, p, 1, next_earnings=LATE_EARNINGS, sector=s) for s, p in names]
    result = screen(snaps, TODAY, replace(LARGE, max_deals=3), ACCOUNT)
    assert len(result.selected) == 3
    assert sum(result.skipped[s] == "max_deals" for s in result.skipped) >= 1

    nearly_full = AccountState(20_000, engaged=9_700)
    result = screen(snaps, TODAY, LARGE, nearly_full)
    engaged = sum(c.quantity * c.collateral for c in result.selected)
    assert engaged <= 300
    assert "capital_limit" in result.skipped.values()


def test_no_second_entry_on_an_open_underlying() -> None:
    snap = make_snapshot("SPY", 500, 1, sector=None)
    result = screen([snap], TODAY, PARAMS, ACCOUNT, open_underlyings={"SPY"})

    assert result.selected == [] and result.skipped == {"SPY": "already_open"}


def test_covered_call_on_assigned_shares() -> None:
    snap = make_snapshot("SOFI", 15, 0.5, iv=0.50, option_types=("put", "call"))
    lot = ShareLot("SOFI", shares=200, cost_basis=15.5, position_id=7)
    result = screen([snap], TODAY, PARAMS, ACCOUNT, share_lots=[lot], open_underlyings={"SOFI"})

    assert result.selected == []
    (cc,) = result.covered_calls
    assert cc.strategy == COVERED_CALL and cc.quantity == 2
    assert cc.legs[0].quote.option_type == "call"
    assert cc.legs[0].quote.strike >= 15.5
    assert cc.collateral == 0 and cc.credit > 0


def test_iv_history_switches_the_rank_method() -> None:
    snap = make_snapshot("SPY", 500, 5, iv=0.30, sector=None)
    history = {"SPY": [0.10 + 0.25 * i / 119 for i in range(120)]}  # 0.10 to 0.35
    result = screen([snap], TODAY, PARAMS, ACCOUNT, iv_history=history)

    rank = result.underlyings["SPY"].iv_rank
    assert rank is not None and rank.method == "history" and 75 < rank.value < 85


def test_true_wheel_only_on_stocks_accepted_for_ownership() -> None:
    params = replace(PARAMS, true_wheel=("SOFI",))
    snaps = [
        make_snapshot(s, 8, 0.5, iv=0.60, next_earnings=LATE_EARNINGS, sector=s)
        for s in ("SOFI", "PLTR")
    ]
    result = screen(snaps, TODAY, replace(params, wheel=("SOFI", "PLTR")), ACCOUNT)

    by_name = {c.underlying: c for c in result.ranked}
    assert by_name["SOFI"].strategy == CASH_SECURED_PUT and by_name["SOFI"].group == "true_wheel"
    assert by_name["PLTR"].strategy == SHORT_PUT and by_name["PLTR"].group == "short_put"
    # No time exit for a put that may be assigned: its window is its whole DTE.
    assert by_name["SOFI"].holding_window == by_name["SOFI"].dte
    assert by_name["PLTR"].holding_window == by_name["PLTR"].dte - PARAMS.exit_dte


def test_the_price_cap_is_no_longer_a_quality_filter() -> None:
    snap = make_snapshot("SOFI", 30, 1, iv=0.60, next_earnings=LATE_EARNINGS)
    params = replace(PARAMS, wheel=("SOFI",), max_trade_risk_pct=0.10, max_trade_pct=0.5)
    assert screen([snap], TODAY, params, ACCOUNT).ranked
    capped = replace(params, use_wheel_max_strike=True)
    assert screen([snap], TODAY, capped, ACCOUNT).ranked == []


def test_an_entry_too_close_to_the_exit_is_rejected() -> None:
    snap = make_snapshot("SPY", 500, 1, sector=None, dtes=(30,))
    params = replace(PARAMS, dte_min=25, dte_max=40, min_holding_days=21)
    result = screen([snap], TODAY, params, ACCOUNT)
    # 30 DTE - 21 DTE exit = 9 days of holding, under the 21-day minimum.
    assert result.ranked == []
    assert result.funnel["holding"] == 0 < result.funnel["dte"]

    allowed = screen([snap], TODAY, replace(params, min_holding_days=5), ACCOUNT)
    assert allowed.ranked and allowed.ranked[0].holding_window == 9


def test_strike_distance_is_given_three_ways() -> None:
    result = screen([make_snapshot("SPY", 500, 1, sector=None)], TODAY, PARAMS, ACCOUNT)
    deal = result.ranked[0]
    strike = deal.short_leg.quote.strike
    assert deal.distance_pct == pytest.approx((500 - strike) / 500)
    assert deal.distance_sd is not None and 0.5 < deal.distance_sd < 2
    assert deal.abs_delta == pytest.approx(abs(deal.short_delta))


def test_put_call_ratio_counts_each_contract_once() -> None:
    snap = make_snapshot("SPY", 500, 1, sector=None)
    puts = [q for q in snap.options if q.option_type == "put"]
    ratio = put_call_ratio([*snap.options, *puts])

    assert ratio is not None
    assert ratio.put_volume == sum(q.volume for q in puts)
    assert ratio.call_volume == 0 and ratio.volume_ratio is None
    assert put_call_ratio([]) is None


def test_put_call_filter_is_off_by_default_and_blocks_heavy_put_volume() -> None:
    heavy = PutCallRatio(put_volume=30_000, call_volume=10_000, put_oi=0, call_oi=0)
    snap = replace(make_snapshot("SPY", 500, 1, sector=None), put_call=heavy)
    on = replace(PARAMS, use_put_call_filter=True, max_put_call_ratio=2.5)

    selected = screen([snap], TODAY, PARAMS, ACCOUNT).selected
    assert [c.underlying for c in selected] == ["SPY"]
    assert selected[0].put_call == heavy
    blocked = screen([snap], TODAY, on, ACCOUNT)
    assert blocked.ranked == [] and blocked.funnel["put_call"] == 0


def test_put_call_filter_lets_through_what_it_cannot_judge() -> None:
    on = replace(PARAMS, use_put_call_filter=True, max_put_call_ratio=2.5)
    thin = PutCallRatio(put_volume=300, call_volume=100, put_oi=0, call_oi=0)
    base = make_snapshot("SPY", 500, 1, sector=None)

    # No data (backtest chains) or too little volume: the filter does not decide.
    for snap in (base, replace(base, put_call=thin)):
        assert [c.underlying for c in screen([snap], TODAY, on, ACCOUNT).selected] == ["SPY"]
