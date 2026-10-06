"""Grid search of the main strategy parameters, guarded against over-fitting.

Usage (offline, on the history cache written by `python -m scripts.backtest fetch`):
    python -m scripts.param_search run --stage entries --out search.jsonl
    python -m scripts.param_search run --stage exits --base '{"delta_min": 0.15}' --out ...
    python -m scripts.param_search analyze search.jsonl

Every combination runs on the whole period (realistic fills); the analysis then only looks at
the calibration part (first 60 % of the days) to choose, prefers a plateau (a combination whose
grid neighbours do well too) to an isolated peak, and judges the choice on the out-of-sample
part and in an anchored walk-forward: for each year, the combination chosen on the years before
it, scored on that year only. The risk limits decided for the live account stay fixed.
"""

import argparse
import itertools
import json
import statistics
from dataclasses import replace
from datetime import date
from multiprocessing import Pool
from pathlib import Path

from app.backtest.data import MarketHistory
from app.backtest.engine import run_backtest
from app.backtest.report import (
    _closed,
    robustness,
    rolling_windows,
    split_periods,
    summarize,
)
from app.backtest.synth import EXECUTION_SCENARIOS, ModelConfig
from app.domain.params import StrategyParams

START, END = date(2019, 1, 2), date(2026, 10, 2)
CAPITAL = 20_000.0
DEFAULT_CACHE = Path(".cache/backtest.json")

# Each dimension: name -> list of (label, overrides). The first stage varies the entries with
# the current exits, the second the exits around the best entries.
STAGES: dict[str, dict[str, list[tuple[str, dict]]]] = {
    "entries": {
        "mix": [
            ("etf+spi", {}),
            ("etf", {"enable_wheel": False}),
            ("spi", {"enable_etfs": False}),
        ],
        "delta": [
            ("0.05-0.15", {"delta_min": 0.05, "delta_max": 0.15}),
            ("0.10-0.20", {"delta_min": 0.10, "delta_max": 0.20}),
            ("0.15-0.25", {"delta_min": 0.15, "delta_max": 0.25}),
            ("0.20-0.30", {"delta_min": 0.20, "delta_max": 0.30}),
        ],
        "dte": [
            ("42-56", {"dte_min": 42, "dte_max": 56}),
            ("45-65", {"dte_min": 45, "dte_max": 65}),
            ("60-80", {"dte_min": 60, "dte_max": 80}),
        ],
        "ivr": [
            ("0", {"min_iv_rank": 0.0}),
            ("30", {"min_iv_rank": 30.0}),
            ("50", {"min_iv_rank": 50.0}),
        ],
    },
    "exits": {
        "tp": [
            ("35 %", {"take_profit_pct": 0.35}),
            ("50 %", {"take_profit_pct": 0.50}),
            ("75 %", {"take_profit_pct": 0.75}),
        ],
        "stop": [
            ("1.5x", {"stop_loss_multiple": 1.5}),
            ("2x", {"stop_loss_multiple": 2.0}),
            ("3x", {"stop_loss_multiple": 3.0}),
        ],
        "exit": [
            ("14 DTE", {"exit_dte": 14}),
            ("21 DTE", {"exit_dte": 21}),
        ],
    },
}
# Categorical dimensions: no smoothing across their values.
CATEGORICAL = {"mix"}

_market: MarketHistory | None = None


def _init(cache: Path) -> None:  # pragma: no cover - worker setup
    global _market
    _market = MarketHistory.load(cache)


def yearly(points: list[tuple[date, float]], capital: float) -> dict[int, float]:
    """Return of each calendar year from the last value of the year before."""
    out, base, year, last = {}, capital, None, capital
    for d, v in points:
        if year is not None and d.year != year:
            out[year] = last / base - 1
            base = last
        year, last = d.year, v
    if year is not None:
        out[year] = last / base - 1
    return out


def evaluate(job: tuple[dict, dict, dict]) -> dict:  # pragma: no cover - worker
    labels, overrides, model = job
    params = replace(StrategyParams(), **overrides)
    errors = params.errors()
    if errors:
        return {"labels": labels, "overrides": overrides, "errors": errors}
    result = run_backtest(_market, params, START, END, CAPITAL, ModelConfig(**model))
    s = summarize(result)
    periods = split_periods(result)
    windows = rolling_windows(result)
    groups: dict[str, float] = {}
    for t in _closed(result.trades):
        groups[t.group] = groups.get(t.group, 0.0) + t.pnl
    return {
        "labels": labels,
        "overrides": overrides,
        "model": model,
        "cagr": s.cagr,
        "max_drawdown": s.max_drawdown,
        "profit_factor": s.profit_factor,
        "sharpe": s.sharpe,
        "trades": s.trades,
        "win_rate": s.win_rate,
        "final": s.final,
        "periods": periods,
        "robustness": robustness(periods, windows),
        "years": yearly([(d, v) for d, v, _ in result.equity], CAPITAL),
        "groups": groups,
    }


def jobs_for(stage: str, base: dict) -> list[tuple[dict, dict, dict]]:
    dims = STAGES[stage]
    jobs = []
    for combo in itertools.product(*dims.values()):
        labels = {name: label for name, (label, _) in zip(dims, combo, strict=True)}
        overrides = dict(base)
        for _, o in combo:
            overrides.update(o)
        jobs.append((labels, overrides, {}))
    return jobs


# --- analysis ---------------------------------------------------------------------------------


def calib_score(row: dict) -> float | None:
    """Calibration CAGR, minus a penalty for drawdown; None when the profile fails the bar."""
    c = row.get("periods", {}).get("calibration")
    if not c or c["trades"] < 30:
        return None
    return c["cagr"] - 0.25 * c["max_drawdown"]


def years_score(row: dict, years: list[int]) -> float | None:
    vals = [row["years"].get(str(y), row["years"].get(y)) for y in years]
    vals = [v for v in vals if v is not None]
    if len(vals) < len(years):
        return None
    # Mean yearly return minus half the spread between years: steady beats lumpy.
    return statistics.mean(vals) - 0.5 * (statistics.pstdev(vals) if len(vals) > 1 else 0)


def neighbours(rows: list[dict], dims: list[str], options: dict[str, list[str]]) -> dict:
    """Index of each row's grid neighbours (one step on one ordered dimension)."""
    key = {tuple(r["labels"][d] for d in dims): i for i, r in enumerate(rows)}
    out = {}
    for i, r in enumerate(rows):
        own = [r["labels"][d] for d in dims]
        near = []
        for j, d in enumerate(dims):
            if d in CATEGORICAL:
                continue
            pos = options[d].index(own[j])
            for step in (-1, 1):
                if 0 <= pos + step < len(options[d]):
                    other = list(own)
                    other[j] = options[d][pos + step]
                    if tuple(other) in key:
                        near.append(key[tuple(other)])
        out[i] = near
    return out


def smoothed(rows, near, score) -> list[float | None]:
    """Plateau score: the mean of a row and its neighbours, None when any of them fails."""
    raw = [score(r) for r in rows]
    out = []
    for i in range(len(rows)):
        vals = [raw[i]] + [raw[j] for j in near[i]]
        out.append(None if any(v is None for v in vals) else statistics.mean(vals))
    return out


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        rows += [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return [r for r in rows if "errors" not in r]


def analyze(paths: list[Path], top: int) -> dict:  # pragma: no cover - CLI
    rows = load(paths)
    dims = list(rows[0]["labels"])
    options = {d: list(dict.fromkeys(r["labels"][d] for r in rows)) for d in dims}
    near = neighbours(rows, dims, options)
    plateau = smoothed(rows, near, calib_score)
    order = sorted(
        (i for i in range(len(rows)) if plateau[i] is not None), key=lambda i: -plateau[i]
    )

    def brief(i):
        r = rows[i]
        c, o = r["periods"]["calibration"], r["periods"]["out_of_sample"]
        return {
            "labels": r["labels"],
            "plateau": plateau[i],
            "calib_cagr": c["cagr"],
            "calib_dd": c["max_drawdown"],
            "calib_pf": c["profit_factor"],
            "oos_cagr": o["cagr"],
            "oos_dd": o["max_drawdown"],
            "oos_pf": o["profit_factor"],
            "cagr": r["cagr"],
            "dd": r["max_drawdown"],
            "trades": r["trades"],
            "verdict": r["robustness"]["label"],
            "positive_windows": r["robustness"]["positive_windows"],
        }

    # Rank correlation between calibration and out-of-sample: does the past rank predict?
    paired = [
        (calib_score(r), r["periods"]["out_of_sample"]["cagr"])
        for r in rows
        if calib_score(r) is not None
    ]
    rank_corr = spearman([a for a, _ in paired], [b for _, b in paired])

    # Anchored walk-forward: choose on the years before, score on the year.
    all_years = sorted({int(y) for r in rows for y in r["years"]})
    wf = []
    for test in all_years[2:]:
        train = [y for y in all_years if y < test]
        sm = smoothed(rows, near, lambda r, train=train: years_score(r, train))
        cands = [i for i in range(len(rows)) if sm[i] is not None]
        if not cands:
            continue
        best = max(cands, key=lambda i: sm[i])
        ret = rows[best]["years"].get(str(test), rows[best]["years"].get(test))
        median = statistics.median(
            r["years"].get(str(test), r["years"].get(test, 0.0)) for r in rows
        )
        wf.append({"year": test, "chosen": rows[best]["labels"], "return": ret, "median": median})
    return {
        "rows": len(rows),
        "top": [brief(i) for i in order[:top]],
        "rank_corr_calib_oos": rank_corr,
        "walk_forward": wf,
        "best_raw_calib": brief(
            max(
                (i for i in range(len(rows)) if calib_score(rows[i]) is not None),
                key=lambda i: calib_score(rows[i]),
            )
        ),
        "best_raw_oos_hindsight": brief(
            max(range(len(rows)), key=lambda i: rows[i]["periods"]["out_of_sample"]["cagr"])
        ),
    }


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None

    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else None


def main() -> None:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--stage", choices=[*STAGES, "scenarios"], required=True)
    run.add_argument("--base", default="{}", help="JSON overrides applied to every combination")
    run.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    run.add_argument("--out", type=Path, required=True)
    an = sub.add_parser("analyze")
    an.add_argument("files", nargs="+", type=Path)
    an.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    if args.command == "analyze":
        print(json.dumps(analyze(args.files, args.top), indent=1, default=str))
        return

    base = json.loads(args.base)
    if args.stage == "scenarios":
        jobs = [({"scenario": k}, base, model) for k, (_, model) in EXECUTION_SCENARIOS.items()]
    else:
        jobs = jobs_for(args.stage, base)
    done = set()
    if args.out.exists():
        done = {
            json.dumps(json.loads(line)["labels"], sort_keys=True)
            for line in args.out.read_text().splitlines()
            if line.strip()
        }
    jobs = [j for j in jobs if json.dumps(j[0], sort_keys=True) not in done]
    print(f"{len(jobs)} backtests à lancer")
    with Pool(initializer=_init, initargs=(args.cache,)) as pool, args.out.open("a") as out:
        for n, row in enumerate(pool.imap_unordered(evaluate, jobs), 1):
            out.write(json.dumps(row, default=str) + "\n")
            out.flush()
            if "errors" in row:
                print(f"[{n}/{len(jobs)}] {row['labels']} invalide : {row['errors'][0]}")
            else:
                print(
                    f"[{n}/{len(jobs)}] {row['labels']} {row['cagr']:+.2%}/an "
                    f"dd {row['max_drawdown']:.1%}",
                    flush=True,
                )


if __name__ == "__main__":  # pragma: no cover
    main()
