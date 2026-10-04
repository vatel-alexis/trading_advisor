"""Backtest the strategy on reconstructed option prices and write a Markdown report.

Usage (the fetch step needs network access to Yahoo Finance; the run step is offline):
    python -m scripts.backtest fetch                     # history -> .cache/backtest.json
    python -m scripts.backtest run --out ../docs         # scenarios -> backtest-resultats.md

Each scenario changes a few strategy parameters (or model assumptions) from the defaults; the
model rows rerun two scenarios with cheaper or richer options to show what depends on them.
"""

import argparse
from dataclasses import replace
from datetime import date
from multiprocessing import Pool
from pathlib import Path

from app.backtest.data import HistoryFetcher, MarketHistory
from app.backtest.engine import BacktestResult, run_backtest
from app.backtest.report import (
    breakdown,
    breakdown_table,
    buy_and_hold,
    markdown_table,
    summarize,
    summary_rows,
    usd,
    write_trades_csv,
)
from app.backtest.synth import ModelConfig
from app.domain.params import StrategyParams

DEFAULT_CACHE = Path(".cache/backtest.json")
START, END = date(2019, 1, 2), date(2026, 10, 2)
CAPITAL = 20_000.0

PRUDENT = {"large_caps": (), "dte_min": 40, "dte_max": 55, "delta_min": 0.10, "delta_max": 0.20}
IV_LOW = {"hv_premium_stock": 1.0, "hv_premium_etf": 1.0, "index_atm_ratio": 0.75}
IV_HIGH = {"hv_premium_stock": 1.4, "hv_premium_etf": 1.4, "index_atm_ratio": 0.95}

# name -> (description, StrategyParams overrides, ModelConfig overrides)
SCENARIOS: dict[str, tuple[str, dict, dict]] = {
    "actuel": ("Paramètres actuels", {}, {}),
    "dte_40_55": ("Entrée à 40-55 DTE", {"dte_min": 40, "dte_max": 55}, {}),
    "sans_stop": ("Sans stop", {"stop_loss_multiple": 1000.0}, {}),
    "stop_3x": ("Stop à 3x le crédit", {"stop_loss_multiple": 3.0}, {}),
    "delta_10_20": ("Delta 0.10-0.20", {"delta_min": 0.10, "delta_max": 0.20}, {}),
    "ivr_0": ("Sans filtre IV Rank", {"min_iv_rank": 0.0}, {}),
    "ivr_50": ("IV Rank > 50", {"min_iv_rank": 50.0}, {}),
    "sortie_14": ("Sortie à 14 DTE", {"exit_dte": 14}, {}),
    "etf_seuls": ("ETF seuls", {"large_caps": (), "wheel": ()}, {}),
    "grandes_valeurs_40": (
        "Grandes valeurs seules, 40-55 DTE",
        {"etfs": (), "wheel": (), "dte_min": 40, "dte_max": 55},
        {},
    ),
    "wheel_40": (
        "Wheel seule, 40-55 DTE",
        {"etfs": (), "large_caps": (), "dte_min": 40, "dte_max": 55},
        {},
    ),
    "etf_wheel_40": (
        "ETF + wheel, 40-55 DTE",
        {"large_caps": (), "dte_min": 40, "dte_max": 55},
        {},
    ),
    "prudent": ("ETF + wheel, 40-55 DTE, delta 0.10-0.20", PRUDENT, {}),
    "actuel_iv_basse": ("Actuel, options moins chères", {}, IV_LOW),
    "actuel_iv_haute": ("Actuel, options plus chères", {}, IV_HIGH),
    "prudent_iv_basse": ("Prudent, options moins chères", PRUDENT, IV_LOW),
    "prudent_iv_haute": ("Prudent, options plus chères", PRUDENT, IV_HIGH),
    "prudent_glissement": (
        "Prudent, entrée à mi-chemin du naturel",
        PRUDENT,
        {"entry_slippage": 0.5},
    ),
}


def run_scenario(args: tuple[str, Path]) -> tuple[str, BacktestResult]:  # pragma: no cover
    name, cache = args
    _, overrides, model = SCENARIOS[name]
    market = MarketHistory.load(cache)
    params = replace(StrategyParams(), **overrides)
    result = run_backtest(market, params, START, END, CAPITAL, ModelConfig(**model))
    return name, result


def render(results: dict[str, BacktestResult], market: MarketHistory) -> str:  # pragma: no cover
    spy = market.symbols["SPY"]
    closes = [(d, c) for d, c in zip(spy.dates, spy.closes, strict=True) if START <= d <= END]
    spy_final, spy_dd = buy_and_hold(closes, CAPITAL)
    rows = []
    for name, r in results.items():
        s = summarize(r)
        rows.append(
            (
                SCENARIOS[name][0],
                usd(s.final),
                f"{s.cagr * 100:.1f} %",
                f"{s.max_drawdown * 100:.1f} %",
                f"{s.sharpe:.2f}",
                s.trades,
                f"{s.win_rate * 100:.0f} %",
                f"{s.profit_factor:.2f}",
                f"{s.avg_days_held:.0f} j",
            )
        )
    base = results["actuel"]
    prudent = results["prudent"]
    parts = [
        "# Résultats du backtest",
        "",
        f"Généré par `python -m scripts.backtest run`, du {START} au {END}, "
        f"capital {usd(CAPITAL)}.",
        "Méthode, hypothèses et limites : voir [backtest.md](backtest.md).",
        "",
        "## Scénarios",
        "",
        markdown_table(
            [
                "Scénario",
                "Final",
                "Rendement/an",
                "Drawdown max",
                "Sharpe",
                "Trades",
                "Gagnants",
                "Profit factor",
                "Durée",
            ],
            rows,
        ),
        "",
        f"Référence : SPY acheté et conservé (hors dividendes) finit à {usd(spy_final)} "
        f"avec un drawdown max de {spy_dd * 100:.1f} %.",
    ]
    for title, r in (("Paramètres actuels", base), ("Scénario prudent", prudent)):
        parts += [
            "",
            f"## {title}",
            "",
            markdown_table(["Mesure", "Valeur"], summary_rows(summarize(r))),
            "",
            breakdown_table("Sortie", breakdown(r.trades, lambda t: t.exit_reason)),
            "",
            breakdown_table("Groupe", breakdown(r.trades, lambda t: t.group)),
            "",
            breakdown_table("Année d'entrée", breakdown(r.trades, lambda t: t.entry_day.year)),
            "",
            breakdown_table(
                "DTE à l'entrée",
                breakdown(r.trades, lambda t: f"{(t.expiration - t.entry_day).days // 5 * 5}+"),
            ),
        ]
    return "\n".join(parts) + "\n"


def main() -> None:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["fetch", "run"])
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=Path("backtest-out"))
    parser.add_argument("--scenarios", nargs="*", default=list(SCENARIOS))
    args = parser.parse_args()

    if args.command == "fetch":
        params = StrategyParams()
        # One year before the start: the IV Rank needs a year of volatility history.
        history = HistoryFetcher(date(START.year - 1, 10, 1), END).fetch(
            params.universe, params.etfs
        )
        history.save(args.cache)
        print(f"Historique enregistré dans {args.cache}")
        return

    names = [n for n in args.scenarios if n in SCENARIOS]
    for required in ("actuel", "prudent"):
        if required not in names:
            names.append(required)
    with Pool() as pool:
        results = dict(pool.map(run_scenario, [(n, args.cache) for n in names]))
    results = {n: results[n] for n in names}
    for name, r in results.items():
        s = summarize(r)
        print(f"{name:<22} final {s.final:>8.0f}  {s.cagr:+.1%}/an  drawdown {s.max_drawdown:.1%}")
    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / "backtest-resultats.md"
    report.write_text(render(results, MarketHistory.load(args.cache)))
    write_trades_csv(results["actuel"].trades, args.out / "backtest-trades-actuel.csv")
    write_trades_csv(results["prudent"].trades, args.out / "backtest-trades-prudent.csv")
    print(f"Rapport : {report}")


if __name__ == "__main__":  # pragma: no cover
    main()
