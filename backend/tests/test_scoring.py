"""Quality scores: absolute, execution and portfolio kept apart, NO TRADE over any score."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.params import StrategyParams
from app.domain.risk import AccountState, Exposure
from app.domain.scoring import LegQuote, assess, finalize, portfolio_fit, return_on_risk
from app.domain.screener import screen
from tests.chains import TODAY, make_snapshot

PARAMS = StrategyParams()
ACCOUNT = AccountState(20_000)
LIQUID = LegQuote(bid=1.98, ask=2.02, iv=0.25, open_interest=1000, volume=200)


def quality(**kw):
    deal = dict(
        strategy="put_credit_spread",
        spot=500.0,
        breakeven=455.0,
        strike=456.0,
        dte=50,
        holding_window=29,
        credit=0.40,
        natural_credit=0.36,
        max_loss=160.0,
        collateral=160.0,
        legs=[LIQUID, LIQUID],
        iv_rank=60.0,
        iv_rank_method="iv_history",
        iv_hv=1.2,
        hv=0.2,
        trend_up=True,
        params=PARAMS,
    )
    deal.update(kw)
    return assess(**deal)


def test_return_on_risk_is_not_annualized() -> None:
    assert return_on_risk("put_credit_spread", 0.40, 160, 160) == 0.25
    assert return_on_risk("short_put", 0.20, 580, 600) == pytest.approx(0.0333, abs=1e-4)
    # The same credit over twice the days does not halve the return.
    short = quality(dte=40, holding_window=19)
    longer = quality(dte=80, holding_window=59)
    assert short.components["return"] == longer.components["return"]


def test_the_final_score_is_the_lowest_of_the_three() -> None:
    q = finalize(quality(), portfolio_fit(0.2), PARAMS)
    assert q.portfolio == 1.0 and q.eligible
    assert q.final == min(q.absolute, q.execution, q.portfolio)
    crowded = finalize(quality(), portfolio_fit(0.95), PARAMS)
    assert crowded.final == crowded.portfolio < q.final
    assert "Adéquation au portefeuille" in crowded.weaknesses


def test_an_illiquid_spread_is_no_trade_whatever_its_return() -> None:
    wide = LegQuote(bid=1.4, ask=2.6, iv=0.25, open_interest=3, volume=0)
    q = finalize(quality(legs=[wide, wide], credit=0.9, natural_credit=0.1), 1.0, PARAMS)
    assert q.components["return"] > 0.9 and q.execution < PARAMS.min_quality_score
    assert not q.eligible
    assert any("Qualité d'exécution" in b for b in q.blocking)
    assert "volume" in q.missing


def test_an_entry_too_close_to_the_exit_scores_low_on_time() -> None:
    assert quality(holding_window=21).components["time"] == pytest.approx(0.3)
    assert quality(holding_window=31).components["time"] == 1.0


def test_missing_data_is_flagged_not_invented() -> None:
    no_oi = LegQuote(bid=1.98, ask=2.02, iv=0.25, open_interest=0, volume=0)
    q = quality(legs=[no_oi], iv_rank=None, iv_rank_method=None, iv_hv=None, trend_up=None)
    assert {"open interest", "volume", "IV Rank"} <= set(q.missing)
    assert q.components["regime"] == 0.5 and q.components["data"] < 0.5


def test_a_high_score_with_a_blocking_rule_is_no_trade() -> None:
    snap = make_snapshot("SPY", 500, 1, sector=None)
    full = [Exposure("QQQ", "ETF", "put_credit_spread", 950, 950, 950)]
    result = screen([snap], TODAY, PARAMS, ACCOUNT, exposures=full)

    (deal,) = result.ranked
    assert deal.quality.pre_score > PARAMS.min_quality_score
    assert result.selected == [] and result.skipped == {"SPY": "cluster_limit"}
    assert deal.quality.blocking and not deal.quality.eligible
    assert deal.quality.rank == 1 and deal.quality.rank_of == 1


def test_illiquid_chains_are_no_trade_in_the_screener() -> None:
    params = replace(
        PARAMS, use_spread_filter=False, use_open_interest_filter=False, use_volume_filter=False
    )
    snap = make_snapshot("SPY", 500, 1, sector=None, spread=0.15, open_interest=2, volume=0)
    result = screen([snap], TODAY, params, ACCOUNT)
    assert result.selected == [] and result.skipped == {"SPY": "low_quality"}
    q = result.ranked[0].quality
    assert q.execution < params.min_quality_score and not q.eligible


def test_selected_deals_carry_their_scores() -> None:
    snaps = [make_snapshot(s, p, 1, sector=None) for s, p in (("SPY", 500), ("QQQ", 400))]
    result = screen(snaps, TODAY, PARAMS, ACCOUNT)
    for deal in result.selected:
        q = deal.quality
        assert q.eligible and deal.score == q.final
        assert set(q.components) == {
            "execution",
            "safety",
            "return",
            "time",
            "regime",
            "data",
            "portfolio",
        }
    # The second deal sees the first in the index cluster: a lower portfolio fit.
    first, second = result.selected
    assert second.quality.portfolio <= first.quality.portfolio
    assert TODAY + timedelta(days=PARAMS.dte_min) <= first.expiration
