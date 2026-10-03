from dataclasses import replace
from datetime import timedelta

from app.domain.params import StrategyParams
from app.domain.risk import AccountState
from app.domain.screener import (
    CASH_SECURED_PUT,
    COVERED_CALL,
    PUT_CREDIT_SPREAD,
    ShareLot,
    screen,
)
from tests.chains import TODAY, make_snapshot

PARAMS = StrategyParams()
ACCOUNT = AccountState(20_000)
LATE_EARNINGS = TODAY + timedelta(days=90)


def test_etf_gets_a_sized_put_credit_spread() -> None:
    result = screen([make_snapshot("SPY", 500, 5, sector=None)], TODAY, PARAMS, ACCOUNT)

    assert [c.underlying for c in result.selected] == ["SPY"]
    deal = result.selected[0]
    short, long = deal.legs
    assert deal.strategy == PUT_CREDIT_SPREAD and deal.sector == "ETF"
    assert short.side == "sell" and long.side == "buy"
    assert short.quote.strike - long.quote.strike == 5
    assert PARAMS.delta_min <= abs(deal.short_delta) <= PARAMS.delta_max
    assert deal.credit >= PARAMS.min_credit
    assert deal.max_loss == (5 - deal.credit) * 100 == deal.collateral
    assert deal.quantity * deal.max_loss <= 2_000
    assert (deal.quantity + 1) * deal.max_loss > 2_000
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
    result = screen(snaps, TODAY, PARAMS, ACCOUNT)

    assert [c.underlying for c in result.ranked] == ["JPM"]


def test_low_iv_rank_rejects_the_underlying() -> None:
    # IV below the whole one-year realized volatility range: rank 0.
    snap = make_snapshot("QQQ", 400, 5, iv=0.08)
    result = screen([snap], TODAY, PARAMS, ACCOUNT)

    assert result.ranked == []
    assert result.funnel["iv_rank"] == 0 < result.funnel["contracts"]


def test_wheel_name_gets_a_cash_secured_put_below_the_strike_cap() -> None:
    snap = make_snapshot("F", 21, 0.5, iv=0.40, next_earnings=LATE_EARNINGS)
    result = screen([snap], TODAY, PARAMS, ACCOUNT)

    deal = result.selected[0]
    assert deal.strategy == CASH_SECURED_PUT and len(deal.legs) == 1
    assert deal.legs[0].quote.strike <= PARAMS.wheel_max_strike
    assert deal.collateral == deal.legs[0].quote.strike * 100
    assert deal.quantity == 1


def test_sector_limit_keeps_two_etfs() -> None:
    etfs = [("SPY", 500), ("QQQ", 400), ("IWM", 200)]
    snaps = [make_snapshot(s, p, 5, sector=None) for s, p in etfs]
    result = screen(snaps, TODAY, PARAMS, ACCOUNT)

    assert len(result.selected) == 2
    assert list(result.skipped.values()) == ["sector_limit"]


def test_max_deals_and_engaged_capital() -> None:
    names = [("AAPL", 300), ("MSFT", 400), ("JPM", 250), ("XOM", 150), ("KO", 100), ("T", 50)]
    snaps = [make_snapshot(s, p, 5, next_earnings=LATE_EARNINGS, sector=s) for s, p in names]
    result = screen(snaps, TODAY, replace(PARAMS, max_deals=3), ACCOUNT)
    assert len(result.selected) == 3
    assert sum(result.skipped[s] == "max_deals" for s in result.skipped) >= 1

    nearly_full = AccountState(20_000, engaged=9_700)
    result = screen(snaps, TODAY, PARAMS, nearly_full)
    engaged = sum(c.quantity * c.collateral for c in result.selected)
    assert engaged <= 300
    assert "capital_limit" in result.skipped.values()


def test_no_second_entry_on_an_open_underlying() -> None:
    snap = make_snapshot("SPY", 500, 5, sector=None)
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
