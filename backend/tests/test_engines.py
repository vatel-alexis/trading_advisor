from datetime import timedelta

import pytest

from app.domain.exits import (
    PROFIT_TARGET,
    STOP_LOSS,
    TIME_EXIT,
    ShortPremium,
    evaluate_exit,
    stop_price,
    take_profit_price,
)
from app.domain.market import atm_iv30, hv30, iv_rank, realized_vol_series
from app.domain.params import StrategyParams
from app.domain.pricing import bs_delta, prob_above, spread_pct
from tests.chains import TODAY, closes_with_vol_range, make_snapshot

PARAMS = StrategyParams()


# --- pricing -------------------------------------------------------------------------------


def test_put_and_call_deltas_have_opposite_signs() -> None:
    put = bs_delta("put", 100, 93, 40 / 365, 0.30, 0.04)
    call = bs_delta("call", 100, 107, 40 / 365, 0.30, 0.04)
    assert -0.5 < put < 0 < call < 0.5


def test_put_call_delta_parity() -> None:
    put = bs_delta("put", 100, 100, 0.25, 0.3, 0.04)
    call = bs_delta("call", 100, 100, 0.25, 0.3, 0.04)
    assert call - put == pytest.approx(1.0)


def test_prob_above_falls_as_the_level_rises() -> None:
    assert prob_above(100, 90, 0.1, 0.3, 0.04) > prob_above(100, 95, 0.1, 0.3, 0.04) > 0.5
    assert prob_above(100, 0, 0.1, 0.3, 0.04) == 1.0


def test_spread_pct_is_relative_to_mid() -> None:
    assert spread_pct(0.95, 1.05) == pytest.approx(0.10)
    assert spread_pct(0, 0) == float("inf")


# --- volatility and IV Rank ----------------------------------------------------------------


def test_realized_vol_tracks_the_generated_range() -> None:
    series = realized_vol_series(closes_with_vol_range(100, 0.10, 0.40))
    assert 0.10 < min(series) < 0.16
    assert max(series) == pytest.approx(0.39, abs=0.02)


def test_iv_rank_uses_realized_vol_proxy_while_history_is_short() -> None:
    closes = closes_with_vol_range(100, 0.10, 0.40)
    rank = iv_rank(0.30, [0.2] * 10, closes, min_history=120)
    assert rank is not None and rank.method == "hv_proxy"
    assert 55 < rank.value < 80


def test_iv_rank_uses_stored_history_once_long_enough() -> None:
    history = [0.20 + 0.20 * i / 119 for i in range(120)]  # 0.20 to 0.40
    rank = iv_rank(0.25, history, [], min_history=120)
    assert rank is not None and rank.method == "history"
    assert rank.value == pytest.approx(25.0)


def test_iv_rank_is_clipped_and_needs_data() -> None:
    closes = closes_with_vol_range(100, 0.10, 0.40)
    assert iv_rank(0.90, [], closes, 120).value == 100.0
    assert iv_rank(0.30, [], closes[:20], 120) is None


def test_atm_iv30_takes_the_expiration_nearest_30_days() -> None:
    snap = make_snapshot("SPY", 500, 5, iv=0.22, dtes=(10, 31, 60))
    assert atm_iv30(snap, TODAY) == pytest.approx(0.22)
    assert hv30(snap.closes) is not None


# --- exits ---------------------------------------------------------------------------------


def _short(strategy: str = "put_credit_spread", dte: int = 40) -> ShortPremium:
    return ShortPremium(strategy, credit=1.00, expiration=TODAY + timedelta(days=dte))


def test_exit_prices() -> None:
    assert take_profit_price(1.00, PARAMS) == 0.50
    assert stop_price(1.00, PARAMS) == 2.00


@pytest.mark.parametrize(
    ("mark", "dte", "expected"),
    [
        (0.80, 40, None),
        (0.50, 40, PROFIT_TARGET),
        (2.00, 40, STOP_LOSS),
        (0.80, 21, TIME_EXIT),
        (2.10, 21, STOP_LOSS),  # a losing position at 21 DTE is a stop
        (0.40, 10, PROFIT_TARGET),
    ],
)
def test_exit_rules(mark: float, dte: int, expected: str | None) -> None:
    signal = evaluate_exit(_short(dte=dte), mark, TODAY, PARAMS)
    assert (signal.reason if signal else None) == expected


def test_covered_call_has_no_stop() -> None:
    signal = evaluate_exit(_short("covered_call"), 3.00, TODAY, PARAMS)
    assert signal is None


def test_a_true_wheel_put_waits_for_assignment_but_takes_its_profit() -> None:
    put = ShortPremium("cash_secured_put", 1.00, TODAY + timedelta(days=21), True)
    assert evaluate_exit(put, 3.00, TODAY, PARAMS) is None  # no stop
    assert evaluate_exit(put, 0.80, TODAY, PARAMS) is None  # no time exit
    assert evaluate_exit(put, 0.40, TODAY, PARAMS).reason == PROFIT_TARGET
    income = _short("short_put", dte=21)
    assert evaluate_exit(income, 0.80, TODAY, PARAMS).reason == TIME_EXIT


# --- params --------------------------------------------------------------------------------


def test_params_round_trip_through_json() -> None:
    data = PARAMS.to_dict()
    assert data["etfs"] == ["SPY", "QQQ", "IWM"]
    assert StrategyParams.from_dict(data) == PARAMS
    assert StrategyParams.from_dict({"max_deals": 3, "unknown": 1}).max_deals == 3
