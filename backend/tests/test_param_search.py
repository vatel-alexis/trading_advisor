from datetime import date

from scripts.param_search import (
    STAGES,
    jobs_for,
    neighbours,
    smoothed,
    spearman,
    yearly,
)


def test_yearly_returns_chain_from_previous_year_end():
    points = [(date(2020, 6, 1), 110.0), (date(2020, 12, 31), 120.0), (date(2021, 3, 1), 132.0)]
    out = yearly(points, 100.0)
    assert round(out[2020], 6) == 0.2
    assert round(out[2021], 6) == 0.1


def test_every_grid_combination_is_a_valid_profile():
    from dataclasses import replace

    from app.domain.params import StrategyParams

    for stage in STAGES:
        for _, overrides, _ in jobs_for(stage, {}):
            assert replace(StrategyParams(), **overrides).errors() == []


def test_plateau_averages_grid_neighbours_only():
    rows = [
        {"labels": {"mix": m, "delta": d}, "score": s}
        for m, d, s in (("a", "1", 1.0), ("a", "2", 3.0), ("a", "3", 5.0), ("b", "1", 100.0))
    ]
    options = {"mix": ["a", "b"], "delta": ["1", "2", "3"]}
    near = neighbours(rows, ["mix", "delta"], options)
    assert sorted(near[1]) == [0, 2]  # no smoothing across the categorical "mix"
    assert smoothed(rows, near, lambda r: r["score"])[1] == 3.0
    assert smoothed(rows, near, lambda r: None if r["score"] > 50 else r["score"])[3] is None


def test_spearman():
    assert round(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 9) == 1.0
    assert round(spearman([1, 2, 3, 4], [4, 3, 2, 1]), 9) == -1.0
