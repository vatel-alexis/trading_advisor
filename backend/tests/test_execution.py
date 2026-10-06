"""Stop signals, expected buy-back price and progressive limits (pure functions, no database)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.exits import (
    BREACH,
    COST,
    DELTA,
    EVENT,
    LIQUIDITY,
    PORTFOLIO,
    STOP_LOSS,
    MarketView,
    ShortPremium,
    evaluate_exit,
    expected_exit,
    stop_rules,
    stop_triggers,
)
from app.domain.orders import price, price_up, progressive_limit
from app.domain.params import StrategyParams
from tests.chains import TODAY

PARAMS = StrategyParams()
SPREAD = ShortPremium("put_credit_spread", 1.00, TODAY + timedelta(days=40))


def view(**values: object) -> MarketView:
    return MarketView(**{"confirmations": 2, **values})


# --- the cost stop works on the expected execution price ---------------------------------------


def test_the_expected_buy_back_sits_between_the_mid_and_the_natural() -> None:
    assert expected_exit(2.00, 2.40, PARAMS) == pytest.approx(2.20)
    assert expected_exit(2.00, None, PARAMS) == 2.00  # no natural: the mid, flagged elsewhere


def test_a_single_mark_at_the_stop_does_not_buy_back() -> None:
    # Mid 2.05 and natural 2.15: expected 2.10, above the 2.00 stop, but on one mark only.
    once = view(natural=2.15, confirmations=1)
    assert evaluate_exit(SPREAD, 2.05, TODAY, PARAMS, once) is None
    twice = view(natural=2.15, confirmations=2)
    signal = evaluate_exit(SPREAD, 2.05, TODAY, PARAMS, twice)
    assert signal.reason == STOP_LOSS and signal.triggers == (COST,)


def test_the_stop_waits_for_the_expected_price_not_the_mid_alone() -> None:
    # The mid moves to 1.98 while the natural is 2.00: expected 1.99, under the 2.00 stop.
    assert COST not in stop_triggers(SPREAD, 1.98, TODAY, PARAMS, view(natural=2.00))
    # A wide natural pushes the expected price over the level.
    assert COST in stop_triggers(SPREAD, 1.98, TODAY, PARAMS, view(natural=2.10))


def test_the_backtest_path_without_a_view_keeps_the_mark_rule() -> None:
    assert evaluate_exit(SPREAD, 2.00, TODAY, PARAMS).triggers == (COST,)
    assert evaluate_exit(SPREAD, 1.99, TODAY, PARAMS) is None


# --- other stop signals ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "trigger"),
    [
        ({"short_delta": -0.55}, DELTA),
        ({"spot": 499.0, "short_strike": 500.0}, BREACH),
        ({"natural": 1.80}, LIQUIDITY),  # losing (mid 1.20 > 1.00) and 0.60 of spread
        ({"next_event": TODAY + timedelta(days=2)}, EVENT),
        ({"portfolio_breach": True}, PORTFOLIO),
    ],
)
def test_each_stop_signal_fires_alone(values: dict, trigger: str) -> None:
    signal = evaluate_exit(SPREAD, 1.20, TODAY, PARAMS, view(**values))
    assert signal.reason == STOP_LOSS and signal.triggers == (trigger,)


@pytest.mark.parametrize(
    ("values", "toggle"),
    [
        ({"short_delta": -0.55}, "use_stop_delta"),
        ({"spot": 499.0, "short_strike": 500.0}, "use_stop_breach"),
        ({"natural": 1.80}, "use_stop_liquidity"),
        ({"next_event": TODAY + timedelta(days=2)}, "use_stop_event"),
        ({"portfolio_breach": True}, "use_stop_portfolio"),
    ],
)
def test_a_signal_switched_off_never_fires(values: dict, toggle: str) -> None:
    params = replace(PARAMS, **{toggle: False})
    assert evaluate_exit(SPREAD, 1.20, TODAY, params, view(**values)) is None


def test_quiet_signals_stay_quiet() -> None:
    calm = view(
        natural=0.85,
        short_delta=-0.20,
        spot=530.0,
        short_strike=500.0,
        next_event=TODAY + timedelta(days=60),  # after the expiration
    )
    assert evaluate_exit(SPREAD, 0.80, TODAY, PARAMS, calm) is None
    # A wide spread or the portfolio limit only stops a losing position.
    assert evaluate_exit(SPREAD, 0.80, TODAY, PARAMS, view(natural=1.40)) is None
    assert evaluate_exit(SPREAD, 0.80, TODAY, PARAMS, view(portfolio_breach=True)) is None


def test_missing_data_never_fires_a_signal() -> None:
    assert stop_triggers(SPREAD, 1.20, TODAY, PARAMS, MarketView()) == ()


def test_held_positions_get_no_stop_signal() -> None:
    put = ShortPremium("cash_secured_put", 1.00, TODAY + timedelta(days=40), True)
    call = ShortPremium("covered_call", 1.00, TODAY + timedelta(days=40))
    stormy = view(natural=3.0, short_delta=-0.9, spot=1.0, short_strike=20.0)
    assert evaluate_exit(put, 2.50, TODAY, PARAMS, stormy) is None
    assert evaluate_exit(call, 2.50, TODAY, PARAMS, stormy) is None
    assert stop_rules(put, PARAMS, 20.0) == [] and stop_rules(call, PARAMS, 20.0) == []


def test_several_signals_are_all_reported() -> None:
    stormy = view(natural=2.30, short_delta=-0.6, spot=495.0, short_strike=500.0)
    signal = evaluate_exit(SPREAD, 2.20, TODAY, PARAMS, stormy)
    assert signal.triggers == (COST, DELTA, BREACH)


def test_stop_rules_describe_the_switched_on_signals() -> None:
    rules = stop_rules(SPREAD, PARAMS, 500.0)
    assert [r["key"] for r in rules] == [COST, DELTA, BREACH, LIQUIDITY, EVENT, PORTFOLIO]
    assert "2.00" in rules[0]["rule"] and "500.00" in rules[2]["rule"]
    assert stop_rules(SPREAD, replace(PARAMS, use_stop_loss=False), 500.0)[0]["key"] == DELTA


# --- progressive limits ------------------------------------------------------------------------


def test_a_credit_steps_from_the_mid_to_the_natural() -> None:
    steps = [price(progressive_limit(1.00, 0.85, s, 3)) for s in range(5)]
    assert steps == [1.00, 0.95, 0.90, 0.85, 0.85]


def test_a_debit_steps_up_to_the_natural() -> None:
    steps = [price_up(progressive_limit(2.15, 2.30, s, 3)) for s in range(1, 4)]
    assert steps == [2.20, 2.25, 2.30]


def test_zero_steps_goes_straight_to_the_natural() -> None:
    assert progressive_limit(2.15, 2.30, 0, 0) == 2.30
    assert progressive_limit(2.15, 2.30, 1, 0) == 2.30


def test_new_parameters_are_valid_by_default() -> None:
    assert PARAMS.errors() == []
    assert StrategyParams.from_dict(PARAMS.to_dict()) == PARAMS
