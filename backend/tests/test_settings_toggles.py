"""Switches of the strategy parameters: each filter, indicator and exit rule can be turned off."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.exits import PROFIT_TARGET, STOP_LOSS, TIME_EXIT, ShortPremium, evaluate_exit
from app.domain.params import PARAM_SPECS, InvalidParams, StrategyParams, parse_params
from app.domain.risk import AccountState, Exposure
from app.domain.screener import screen, trend_ok
from tests.chains import TODAY, make_snapshot

PARAMS = StrategyParams()
ACCOUNT = AccountState(20_000)
LATE_EARNINGS = TODAY + timedelta(days=90)


def test_every_field_has_a_spec_and_every_toggle_exists() -> None:
    names = set(PARAMS.to_dict())
    specs = {s.key for s in PARAM_SPECS}
    # Internal knobs not shown in the settings page.
    assert names - specs == {"iv_rank_min_history", "risk_free_rate"}
    assert all(s.toggle in names for s in PARAM_SPECS if s.toggle)
    assert PARAMS.errors() == []


def test_new_indicators_start_off_and_old_filters_on() -> None:
    assert not PARAMS.use_trend_filter and not PARAMS.use_iv_hv_filter
    assert PARAMS.use_iv_rank_filter and PARAMS.use_earnings_filter and PARAMS.use_stop_loss
    assert PARAMS.use_sector_limit and PARAMS.reentry_cooldown_days == 0


def test_disabled_group_leaves_the_universe() -> None:
    params = replace(PARAMS, enable_large_caps=False)
    assert "AAPL" not in params.universe and params.group_of("AAPL") is None
    assert "AAPL" in params.all_symbols
    assert params.group_of("SPY") == "etf"


def test_parse_params_types_and_cleans_form_values() -> None:
    params = parse_params(
        {"dte_min": "40", "delta_max": "0.2", "etfs": "spy, qqq,SPY", "spread_widths": "5;10"}
    )
    assert params.dte_min == 40 and params.delta_max == 0.2
    assert params.etfs == ("SPY", "QQQ") and params.spread_widths == (5.0, 10.0)
    # Unspecified keys keep the base values.
    assert parse_params({}, replace(PARAMS, max_deals=3)).max_deals == 3


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"dte_min": 2.5}, "DTE min à l'entrée : valeur invalide"),
        ({"use_stop_loss": "oui"}, r"Stop \(hors covered calls\) : valeur invalide"),
        ({"dte_min": 60}, "DTE min doit être inférieur"),
        ({"min_iv_rank": 150}, "maximum 100"),
        ({"enable_etfs": False, "enable_large_caps": False, "enable_wheel": False}, "Aucun titre"),
        ({"etfs": "SPY, $$"}, "Liste des ETF : valeur invalide"),
    ],
)
def test_parse_params_refuses_bad_values(data: dict, message: str) -> None:
    with pytest.raises(InvalidParams, match=message):
        parse_params(data)


# --- exits --------------------------------------------------------------------------------


def _short(dte: int = 40) -> ShortPremium:
    return ShortPremium("put_credit_spread", 1.00, TODAY + timedelta(days=dte))


@pytest.mark.parametrize(
    ("switch", "mark", "dte", "fires"),
    [
        ("use_stop_loss", 2.5, 40, STOP_LOSS),
        ("use_take_profit", 0.3, 40, PROFIT_TARGET),
        ("use_time_exit", 0.8, 15, TIME_EXIT),
    ],
)
def test_switched_off_exit_never_fires(switch: str, mark: float, dte: int, fires: str) -> None:
    assert evaluate_exit(_short(dte), mark, TODAY, PARAMS).reason == fires
    assert evaluate_exit(_short(dte), mark, TODAY, replace(PARAMS, **{switch: False})) is None


# --- screener -----------------------------------------------------------------------------


def test_iv_rank_filter_off_lets_a_cheap_underlying_through() -> None:
    snap = make_snapshot("QQQ", 400, 5, iv=0.08)
    assert screen([snap], TODAY, PARAMS, ACCOUNT).ranked == []
    result = screen([snap], TODAY, replace(PARAMS, use_iv_rank_filter=False), ACCOUNT)
    assert [c.underlying for c in result.ranked] == ["QQQ"]


def test_earnings_filter_off_keeps_a_stock_reporting_before_expiration() -> None:
    snap = make_snapshot("AAPL", 300, 5, next_earnings=TODAY + timedelta(days=20))
    large = replace(PARAMS, enable_large_caps=True)
    assert screen([snap], TODAY, large, ACCOUNT).ranked == []
    result = screen([snap], TODAY, replace(large, use_earnings_filter=False), ACCOUNT)
    assert [c.underlying for c in result.ranked] == ["AAPL"]


def test_trend_filter_compares_the_close_with_its_average() -> None:
    on = replace(PARAMS, use_trend_filter=True, trend_sma_days=3)
    assert trend_ok([10, 10, 10, 11], on)
    assert not trend_ok([12, 12, 12, 11], on)
    assert not trend_ok([11], on)  # too short a history
    assert trend_ok([12, 12, 12, 11], PARAMS)  # off


def test_trend_filter_rejects_a_falling_underlying() -> None:
    snap = make_snapshot("SPY", 500, 5, sector=None)
    falling = replace(
        snap, closes=[c * (2 - i / len(snap.closes)) for i, c in enumerate(snap.closes)]
    )
    params = replace(PARAMS, use_trend_filter=True, trend_sma_days=50)
    result = screen([falling], TODAY, params, ACCOUNT)
    assert result.ranked == [] and result.funnel["trend"] == 0 < result.funnel["iv_rank"]


def test_iv_hv_filter_needs_implied_above_realized() -> None:
    snap = make_snapshot("SPY", 500, 5, sector=None)
    loose = replace(PARAMS, use_iv_hv_filter=True, min_iv_hv_ratio=0.1)
    strict = replace(PARAMS, use_iv_hv_filter=True, min_iv_hv_ratio=10)
    assert screen([snap], TODAY, loose, ACCOUNT).ranked
    assert screen([snap], TODAY, strict, ACCOUNT).ranked == []


def test_sector_limit_counts_open_positions_or_can_be_off() -> None:
    snaps = [make_snapshot(s, p, 1, sector=None) for s, p in [("SPY", 500), ("QQQ", 400)]]
    assert len(screen(snaps, TODAY, PARAMS, ACCOUNT).selected) == 2
    # An open IWM spread already uses one of the two ETF places.
    iwm = Exposure("IWM", "ETF", "put_credit_spread", 150, 150, 150)
    result = screen(snaps, TODAY, PARAMS, ACCOUNT, exposures=[iwm])
    assert len(result.selected) == 1 and "sector_limit" in result.skipped.values()
    three = [*snaps, make_snapshot("IWM", 200, 1, sector=None)]
    assert len(screen(three, TODAY, replace(PARAMS, use_sector_limit=False), ACCOUNT).selected) == 3


def test_cooling_down_underlying_is_skipped() -> None:
    snap = make_snapshot("SPY", 500, 5, sector=None)
    result = screen([snap], TODAY, PARAMS, ACCOUNT, cooling_down={"SPY"})
    assert result.selected == [] and result.skipped == {"SPY": "cooldown"}


def test_liquidity_filters_can_be_switched_off() -> None:
    snap = make_snapshot("SPY", 500, 5, sector=None, open_interest=10)
    assert screen([snap], TODAY, PARAMS, ACCOUNT).ranked == []
    off = replace(PARAMS, use_open_interest_filter=False)
    assert screen([snap], TODAY, off, ACCOUNT).ranked
