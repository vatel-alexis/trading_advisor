"""Risk measures, contract sizing and portfolio limits (open positions included)."""

from dataclasses import replace

import pytest

from app.domain.params import StrategyParams
from app.domain.risk import (
    INDEX_CLUSTER,
    NO_TRADE_MESSAGES,
    Exposure,
    Portfolio,
    check_limits,
    cluster_of,
    expiration_concentration,
    put_spread_stress_loss,
    short_put_stress_loss,
    size_deal,
    trade_risk,
)
from tests.chains import TODAY

PARAMS = StrategyParams()
EMPTY = Portfolio(20_000)


def spread(underlying="SPY", sector="ETF", max_loss=160.0, quantity=1) -> Exposure:
    total = max_loss * quantity
    return Exposure(underlying, sector, "put_credit_spread", total, total, total)


def size(portfolio=EMPTY, params=PARAMS, **kw):
    deal = dict(
        underlying="SPY",
        sector="ETF",
        strategy="put_credit_spread",
        max_loss=160.0,
        stress_loss=160.0,
        collateral=160.0,
    )
    deal.update(kw)
    return size_deal(portfolio=portfolio, params=params, **deal)


# --- max loss and stress loss --------------------------------------------------------------


def test_max_loss_of_a_spread_is_width_minus_credit_and_stress_caps_at_it() -> None:
    # 2 wide, 0.40 credit: 160 $ of max loss.
    assert put_spread_stress_loss(500, 490, 488, 0.40, 0.15) == pytest.approx(160)
    # A 1 % fall leaves the short strike out of the money: no loss.
    assert put_spread_stress_loss(500, 490, 488, 0.40, 0.01) == 0
    assert trade_risk("put_credit_spread", 160, 0) == 160


def test_a_short_put_is_sized_on_its_stress_loss_not_on_stock_to_zero() -> None:
    # Strike 10, credit 0.30, spot 11: a 30 % gap down to 7.70.
    stress = short_put_stress_loss(11, 10, 0.30, 0.30)
    assert stress == pytest.approx((10 - 7.7 - 0.3) * 100)
    contractual = (10 - 0.30) * 100
    assert stress < contractual < 10 * 100  # stress < max loss < collateral
    assert trade_risk("cash_secured_put", contractual, stress) == stress
    assert trade_risk("covered_call", 0, 0) == 0  # the shares carry the risk


# --- number of contracts -------------------------------------------------------------------


def test_contracts_are_the_floor_of_budget_over_risk_per_contract() -> None:
    # 1 % of 20 000 = 200 $ of budget.
    assert size(max_loss=60, stress_loss=60, collateral=60).quantity == 3  # 200 / 60 = 3.33
    assert size(max_loss=100, stress_loss=100, collateral=100).quantity == 2
    sizing = size(max_loss=199, stress_loss=199, collateral=199)
    assert sizing.quantity == 1 and sizing.binding == "risk_budget"


def test_no_contract_within_the_budget_is_no_trade() -> None:
    sizing = size(max_loss=380, stress_loss=380, collateral=380)
    assert sizing.quantity == 0 and sizing.no_trade == "risk_budget"
    assert sizing.no_trade in NO_TRADE_MESSAGES


def test_the_exceptional_budget_needs_the_switch_and_the_score() -> None:
    on = replace(PARAMS, use_exceptional_risk=True)
    assert size(params=on, score=0.5, max_loss=380, stress_loss=380, collateral=380).quantity == 0
    assert size(params=on, score=0.9, max_loss=380, stress_loss=380, collateral=380).quantity == 1
    assert size(score=0.9, max_loss=380, stress_loss=380, collateral=380).quantity == 0


# --- cumulative risk, clusters, sectors ----------------------------------------------------


def test_open_positions_count_in_the_total_open_max_loss() -> None:
    # 10 % of 20 000 = 2 000 $; 1 900 already open in other clusters leaves room for none.
    open_ = Portfolio(
        20_000,
        (
            Exposure("SOFI", "Technology", "cash_secured_put", 950, 150, 1000),
            Exposure("F", "Consumer Cyclical", "cash_secured_put", 950, 150, 1000),
        ),
    )
    sizing = size(open_, max_loss=160, stress_loss=160, collateral=160)
    assert sizing.quantity == 0 and sizing.no_trade == "open_risk_limit"
    assert sizing.caps["risk_budget"] == 1


def test_the_index_cluster_counts_spy_qqq_and_iwm_together() -> None:
    assert cluster_of("QQQ", "ETF", PARAMS) == cluster_of("IWM", None, PARAMS) == INDEX_CLUSTER
    assert cluster_of("JPM", "Financial Services", PARAMS) == "Financial Services"
    # 5 % of 20 000 = 1 000 $ per cluster: 900 open on QQQ and IWM leaves 100.
    open_ = Portfolio(20_000, (spread("QQQ", max_loss=450), spread("IWM", max_loss=450)))
    sizing = size(open_, max_loss=160, stress_loss=160, collateral=160)
    assert sizing.quantity == 0 and sizing.no_trade == "cluster_limit"
    assert open_.clusters(PARAMS) == {INDEX_CLUSTER: 900}


def test_check_limits_rejects_what_the_open_book_no_longer_allows() -> None:
    book = Portfolio(20_000, (spread("QQQ"), spread("IWM")))
    new = spread("SPY", max_loss=160)
    assert check_limits(new, book, PARAMS) == ["sector_limit"]  # 2 ETFs open already
    assert check_limits(new, book, replace(PARAMS, use_sector_limit=False)) == []
    assert "already_open" in check_limits(spread("QQQ"), book, PARAMS)
    big = spread("XOM", "Energy", max_loss=1_100)
    assert set(check_limits(big, book, PARAMS)) == {"cluster_limit"}


def test_expiration_concentration_sums_max_loss_by_date() -> None:
    a = replace(spread("SPY"), expiration=TODAY)
    b = replace(spread("QQQ", max_loss=100), expiration=TODAY)
    assert expiration_concentration([a, b]) == {TODAY.isoformat(): 260}
