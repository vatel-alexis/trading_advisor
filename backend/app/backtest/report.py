"""Performance figures of a backtest run, and their rendering as Markdown and CSV.

Besides the usual figures: the headline risk measures (drawdown, profit factor, worst year,
losing streak, recovery time), a calibration / out-of-sample split and rolling 12-month
windows (robustness), stress tests replayed on the trades, and what the data can and cannot
tell (reconstructed prices, untested filters).
"""

import csv
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from app.backtest.data import VOL_INDEX
from app.backtest.engine import ASSIGNMENT, CALLED_AWAY, SHARES, BacktestResult, Trade

# Version of the simulation: 2 simulates the whole True Wheel (shares and covered calls), the
# loss limits, the stop signals and the realistic entry fills. Older runs are not comparable.
ENGINE_VERSION = 2
CALIBRATION_SHARE = 0.6
WINDOW_MONTHS = 12
WINDOW_STEP_MONTHS = 3
# Filters a reconstructed chain cannot test: every synthetic quote is liquid.
UNTESTED_FILTERS = (
    ("use_open_interest_filter", "Open interest minimum (pas d'historique d'open interest)"),
    ("use_volume_filter", "Volume minimum (pas d'historique de volume)"),
    ("use_spread_filter", "Écart bid/ask maximum (écarts reconstitués, pas historiques)"),
    ("use_stop_liquidity", "Stop sur la liquidité (écarts reconstitués, pas historiques)"),
)
APPROXIMATIONS = (
    "Prix d'options reconstitués par Black-Scholes : ce ne sont pas des cotations historiques.",
    "Volatilité implicite : VIX / VXN pour SPY et QQQ, sinon volatilité réalisée x prime.",
    "Écarts bid/ask : proportion fixe du prix, calibrée sur les chaînes du 02/10/2026.",
    "Stops testés sur les extrêmes du jour et la clôture : pas de confirmation intrajournalière.",
    "Dates de résultats passées en partie estimées (un rapport par trimestre).",
)


@dataclass(frozen=True)
class Summary:
    start: date
    end: date
    capital: float
    final: float
    cagr: float
    max_drawdown: float
    sharpe: float
    trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_days_held: float
    avg_engaged_pct: float
    days_with_deal_pct: float


def max_drawdown(values: Sequence[float]) -> float:
    """Largest peak-to-trough fall, as a fraction of the peak."""
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def cagr(start_value: float, end_value: float, days: int) -> float:
    if start_value <= 0 or days <= 0:
        return 0.0
    if end_value <= 0:
        return -1.0
    return (end_value / start_value) ** (365 / days) - 1


def sharpe(values: Sequence[float]) -> float:
    """Annualized mean over volatility of daily returns, with no risk-free rate."""
    rets = [b / a - 1 for a, b in zip(values, values[1:], strict=False) if a > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    return mean / sd * math.sqrt(252) if sd > 0 else 0.0


def _closed(trades: Iterable) -> list:
    """Closed trades and share lots, in exit order."""
    return sorted(
        (t for t in trades if t.exit_day is not None), key=lambda t: (t.exit_day, t.entry_day)
    )


def profit_factor(pnls: Iterable[float]) -> float:
    """Gross gains over gross losses; infinite with no loss."""
    values = list(pnls)
    gains = sum(p for p in values if p > 0)
    losses = -sum(p for p in values if p <= 0)
    return gains / losses if losses > 0 else math.inf


def max_consecutive_losses(pnls: Iterable[float]) -> int:
    streak = worst = 0
    for p in pnls:
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)
    return worst


def recovery(points: Sequence[tuple[date, float]]) -> tuple[int, bool]:
    """Longest stretch, in calendar days, below a previous peak, and whether it was recovered
    (False when the run ends under water)."""
    peak_day, peak = None, -math.inf
    longest, recovered = 0, True
    for day, value in points:
        if value >= peak:
            if peak_day is not None:
                longest = max(longest, (day - peak_day).days)
            peak_day, peak = day, value
            continue
    if points and points[-1][1] < peak:
        open_days = (points[-1][0] - peak_day).days
        if open_days >= longest:
            longest, recovered = open_days, False
    return longest, recovered


def summarize(result: BacktestResult) -> Summary:
    closed = [t for t in result.trades if t.exit_day is not None]
    values = [v for _, v, _ in result.equity]
    wins = [t.pnl for t in closed if t.pnl > 0]
    losses = [t.pnl for t in closed if t.pnl <= 0]
    start, end = result.equity[0][0], result.equity[-1][0]
    engaged = [e / v for _, v, e in result.equity if v > 0]
    return Summary(
        start=start,
        end=end,
        capital=result.capital,
        final=values[-1],
        cagr=cagr(result.capital, values[-1], (end - start).days),
        max_drawdown=max_drawdown([result.capital, *values]),
        sharpe=sharpe([result.capital, *values]),
        trades=len(closed),
        win_rate=len(wins) / len(closed) if closed else 0.0,
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        profit_factor=profit_factor(t.pnl for t in closed),
        avg_days_held=sum(t.days_held for t in closed) / len(closed) if closed else 0.0,
        avg_engaged_pct=sum(engaged) / len(engaged) if engaged else 0.0,
        days_with_deal_pct=result.days_with_deal / len(result.equity) if result.equity else 0.0,
    )


def breakdown(trades: Iterable[Trade], key: Callable[[Trade], object]) -> list[tuple]:
    """(key, trades, win rate, total P&L, average P&L) per key, sorted by key."""
    groups: dict[object, list[Trade]] = defaultdict(list)
    for t in trades:
        if t.exit_day is not None:
            groups[key(t)].append(t)
    rows = []
    for k in sorted(groups, key=str):
        ts = groups[k]
        total = sum(t.pnl for t in ts)
        rows.append((k, len(ts), sum(t.pnl > 0 for t in ts) / len(ts), total, total / len(ts)))
    return rows


def buy_and_hold(closes: Sequence[tuple[date, float]], capital: float) -> tuple[float, float]:
    """Final value and max drawdown of `capital` invested at the first close (no dividends)."""
    values = [capital * c / closes[0][1] for _, c in closes]
    return values[-1], max_drawdown(values)


def _pct(x: float) -> str:
    return f"{x * 100:.1f} %"


def usd(x: float) -> str:
    return f"{x:,.0f} $".replace(",", " ")


def summary_rows(s: Summary) -> list[tuple[str, str]]:
    return [
        ("Période", f"{s.start} → {s.end}"),
        ("Capital initial → final", f"{usd(s.capital)} → {usd(s.final)}"),
        ("Rendement annualisé", _pct(s.cagr)),
        ("Drawdown max", _pct(s.max_drawdown)),
        ("Sharpe (journalier, sans taux)", f"{s.sharpe:.2f}"),
        ("Trades clôturés", str(s.trades)),
        ("Trades gagnants", _pct(s.win_rate)),
        ("Gain moyen / perte moyenne", f"{usd(s.avg_win)} / {usd(s.avg_loss)}"),
        ("Profit factor", f"{s.profit_factor:.2f}"),
        ("Durée moyenne", f"{s.avg_days_held:.1f} jours"),
        ("Capital engagé moyen", _pct(s.avg_engaged_pct)),
        ("Jours avec au moins un deal", _pct(s.days_with_deal_pct)),
    ]


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def breakdown_table(title: str, rows: list[tuple]) -> str:
    return markdown_table(
        [title, "Trades", "Gagnants", "P&L total", "P&L moyen"],
        [(k, n, _pct(w), usd(total), usd(avg)) for k, n, w, total, avg in rows],
    )


def write_trades_csv(trades: Iterable[Trade], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "underlying",
                "strategy",
                "entry_day",
                "expiration",
                "strikes",
                "quantity",
                "credit",
                "short_delta",
                "pop",
                "iv_rank",
                "exit_day",
                "exit_reason",
                "exit_price",
                "pnl",
            ]
        )
        for t in trades:
            w.writerow(
                [
                    t.underlying,
                    t.strategy,
                    t.entry_day,
                    t.expiration,
                    "/".join(f"{k:g}" for k, _ in t.legs),
                    t.quantity,
                    round(t.credit, 4),
                    round(t.short_delta, 4),
                    round(t.pop, 4),
                    None if t.iv_rank is None else round(t.iv_rank, 1),
                    t.exit_day,
                    t.exit_reason,
                    None if t.exit_price is None else round(t.exit_price, 4),
                    round(t.pnl, 2),
                ]
            )


# --- robustness, stress tests and data quality -------------------------------------------------


def _months_later(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    for d in (28, 29, 30, 31):
        try:
            last = date(year, month, d)
        except ValueError:
            break
    return min(date(year, month, min(day.day, last.day)), last)


def period_metrics(
    points: Sequence[tuple[date, float]], trades: Sequence, base: float
) -> dict[str, object]:
    """Return, drawdown and trade figures of one stretch of the curve, from `base`."""
    if not points:
        return {}
    start, end = points[0][0], points[-1][0]
    final = points[-1][1]
    pnls = [t.pnl for t in trades]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "net_return": _num(final / base - 1) if base > 0 else None,
        "cagr": _num(cagr(base, final, (end - start).days)),
        "max_drawdown": _num(max_drawdown([base, *(v for _, v in points)])),
        "profit_factor": _num(profit_factor(pnls), 2),
        "trades": len(pnls),
        "win_rate": _num(sum(p > 0 for p in pnls) / len(pnls)) if pnls else None,
    }


def split_periods(result: BacktestResult, share: float = CALIBRATION_SHARE) -> dict[str, dict]:
    """Calibration (first `share` of the days) and out-of-sample (the rest) figures.

    The settings are not re-optimized on the first part: the split shows whether the results
    hold on days the settings were not looked at, when they were chosen on the first ones.
    """
    points = [(d, v) for d, v, _ in result.equity]
    if len(points) < 2:
        return {}
    cut = max(1, min(len(points) - 1, int(len(points) * share)))
    split_day = points[cut][0]
    closed = _closed(result.trades)
    first = [t for t in closed if t.entry_day < split_day]
    second = [t for t in closed if t.entry_day >= split_day]
    return {
        "calibration": period_metrics(points[:cut], first, result.capital),
        "out_of_sample": period_metrics(points[cut:], second, points[cut - 1][1]),
    }


def rolling_windows(
    result: BacktestResult, months: int = WINDOW_MONTHS, step: int = WINDOW_STEP_MONTHS
) -> list[dict]:
    """Return and drawdown of every `months`-long window, one starting every `step` months."""
    points = [(d, v) for d, v, _ in result.equity]
    if not points:
        return []
    out = []
    start = points[0][0]
    previous = result.capital
    while _months_later(start, months) <= points[-1][0]:
        end = _months_later(start, months)
        window = [(d, v) for d, v in points if start <= d <= end]
        before = [v for d, v in points if d < start]
        base = before[-1] if before else previous
        if window:
            out.append(
                {
                    "start": window[0][0].isoformat(),
                    "end": window[-1][0].isoformat(),
                    "return": _num(window[-1][1] / base - 1) if base > 0 else None,
                    "max_drawdown": _num(max_drawdown([base, *(v for _, v in window)])),
                }
            )
        start = _months_later(start, step)
    return out


def robustness(periods: dict[str, dict], windows: Sequence[dict]) -> dict[str, object]:
    """A plain verdict: share of positive 12-month windows and the out-of-sample result."""
    returns = [w["return"] for w in windows if w.get("return") is not None]
    positive = sum(r > 0 for r in returns) / len(returns) if returns else None
    oos = periods.get("out_of_sample") or {}
    oos_return, oos_pf = oos.get("net_return"), oos.get("profit_factor")
    oos_ok = oos_return is not None and oos_return > 0 and (oos_pf is None or oos_pf >= 1)
    if positive is None or not oos:
        label = "insuffisant"
    elif positive >= 0.75 and oos_ok:
        label = "robuste"
    elif positive < 0.5 or not oos_ok:
        label = "fragile"
    else:
        label = "moyen"
    return {
        "label": label,
        "positive_windows": _num(positive) if positive is not None else None,
        "windows": len(returns),
        "out_of_sample_positive": oos_ok if oos else None,
    }


def _curve_figures(capital: float, pnls_by_day: Sequence[tuple[date, float]]) -> dict:
    """Net return, drawdown, profit factor and win rate of realized P&L in exit order."""
    values, total = [capital], capital
    for _, p in pnls_by_day:
        total += p
        values.append(total)
    pnls = [p for _, p in pnls_by_day]
    return {
        "net_return": _num(total / capital - 1) if capital > 0 else None,
        "max_drawdown": _num(max_drawdown(values)),
        "profit_factor": _num(profit_factor(pnls), 2),
        "win_rate": _num(sum(p > 0 for p in pnls) / len(pnls)) if pnls else None,
    }


def stress_tests(result: BacktestResult) -> list[dict]:
    """Replays of the closed trades under harder conditions (realized P&L, exit order).

    The entries are not re-run: a stressed trade keeps its size and dates, so these are
    first-order effects, not new simulations.
    """
    closed = _closed(result.trades)
    model = result.model
    base = [(t.exit_day, t.pnl) for t in closed]

    def cost(t, entry: float, exit_: float) -> float:
        return (entry * t.entry_spread + exit_ * t.exit_spread) * 100 * t.quantity

    plus_half = [(t.exit_day, t.pnl - cost(t, 1.0, 1.0)) for t in closed]
    wider = [
        (t.exit_day, t.pnl - cost(t, model.entry_slippage, model.exit_slippage)) for t in closed
    ]
    rows = [
        {"key": "base", "label": "Référence (P&L réalisé)", **_curve_figures(result.capital, base)},
        {
            "key": "slippage",
            "label": "Glissement + 1 demi-écart à l'entrée et aux stops / sorties",
            **_curve_figures(result.capital, plus_half),
        },
        {
            "key": "spreads",
            "label": "Écarts bid/ask doublés",
            **_curve_figures(result.capital, wider),
        },
    ]
    losses = [t.pnl for t in closed if t.pnl <= 0]
    wins = [t.pnl for t in closed if t.pnl > 0]
    average_loss = sum(losses) / len(losses) if losses else -(sum(wins) / len(wins) if wins else 0)
    for points in (5, 10):
        flips = round(len(closed) * points / 100)
        winners = [i for i, t in enumerate(closed) if t.pnl > 0]
        chosen = set()
        if flips and winners:
            chosen = set(winners[:: max(1, len(winners) // flips)][:flips])
        pnls = [(t.exit_day, average_loss if i in chosen else t.pnl) for i, t in enumerate(closed)]
        rows.append(
            {
                "key": f"win_rate_{points}",
                "label": f"Taux de gain - {points} points (gagnants changés en perte moyenne)",
                **_curve_figures(result.capital, pnls),
            }
        )
    rows.append(correlated_losses(result))
    return rows


def correlated_losses(result: BacktestResult) -> dict:
    """Every open position losing its stress loss on the same day, at the worst day."""
    values = {d: v for d, v, _ in result.equity}
    peak, worst = result.capital, None
    for day, _, stress in result.open_risk:
        value = values.get(day)
        if value is None:
            continue
        peak = max(peak, value)
        drawdown = (peak - (value - stress)) / peak if peak > 0 else 0.0
        if worst is None or drawdown > worst[0]:
            worst = (drawdown, day, stress, stress / value if value > 0 else None)
    row = {
        "key": "correlated",
        "label": "Pertes corrélées : toutes les positions ouvertes perdent leur stress loss le "
        "même jour",
    }
    if worst is None:
        return {**row, "max_drawdown": None, "loss": None, "loss_pct": None, "date": None}
    drawdown, day, stress, share = worst
    return {
        **row,
        "max_drawdown": _num(max(drawdown, max_drawdown([result.capital, *values.values()]))),
        "loss": round(stress, 2),
        "loss_pct": _num(share) if share is not None else None,
        "date": day.isoformat(),
    }


def data_quality(result: BacktestResult) -> dict[str, object]:
    """How far the run can be trusted: never better than medium on reconstructed prices."""
    trades = [t for t in result.trades if t.strategy != SHARES]
    on_index = sum(t.underlying in VOL_INDEX for t in trades)
    share = on_index / len(trades) if trades else 0.0
    return {
        "level": "moyenne" if trades and share >= 0.8 else "faible",
        "prices": "reconstitués",
        "iv_from_index": _num(share),
        "untested_filters": [
            label for key, label in UNTESTED_FILTERS if getattr(result.params, key, False)
        ],
        "approximations": list(APPROXIMATIONS),
    }


def wheel_summary(result: BacktestResult) -> dict[str, object] | None:
    """Puts, covered calls and shares of the True Wheel, each one's P&L and the total."""
    puts = [t for t in result.trades if t.strategy == "cash_secured_put"]
    if not puts:
        return None
    calls = [t for t in result.trades if t.strategy == "covered_call"]
    lots = [t for t in result.trades if t.strategy == SHARES]
    parts = {
        "puts_pnl": sum(t.pnl for t in puts),
        "calls_pnl": sum(t.pnl for t in calls),
        "shares_pnl": sum(t.pnl for t in lots),
    }
    return {
        "complete": True,
        "puts": len(puts),
        "assignments": sum(t.exit_reason == ASSIGNMENT for t in puts),
        "covered_calls": len(calls),
        "called_away": sum(t.exit_reason == CALLED_AWAY for t in calls),
        "lots_open_at_end": sum(t.exit_day is None for t in lots),
        **{k: round(v, 2) for k, v in parts.items()},
        "total_pnl": round(sum(parts.values()), 2),
    }


def headline(result: BacktestResult, summary: Summary) -> dict:
    closed = _closed(result.trades)
    years = yearly_returns(result.equity, result.capital)
    worst_year = min(
        (y for y in years if y["return"] is not None), key=lambda y: y["return"], default=None
    )
    days, recovered = recovery([(result.equity[0][0] - timedelta(days=1), result.capital)]
                               + [(d, v) for d, v, _ in result.equity])  # fmt: skip
    return {
        "net_return": _num(summary.final / summary.capital - 1) if summary.capital else None,
        "worst_year": worst_year,
        "max_consecutive_losses": max_consecutive_losses(t.pnl for t in closed),
        "recovery_days": days,
        "recovered": recovered,
    }


def summary_figures(result: BacktestResult) -> dict:
    """The comparable figures of a run (also what an execution scenario keeps)."""
    s = summarize(result)
    windows = rolling_windows(result)
    periods = split_periods(result)
    head = headline(result, s)
    return {
        "cagr": _num(s.cagr),
        "net_return": head["net_return"],
        "max_drawdown": _num(s.max_drawdown),
        "profit_factor": _num(s.profit_factor, 2),
        "trades": s.trades,
        "win_rate": _num(s.win_rate),
        "worst_year": head["worst_year"],
        "max_consecutive_losses": head["max_consecutive_losses"],
        "recovery_days": head["recovery_days"],
        "recovered": head["recovered"],
        "robustness": robustness(periods, windows),
    }


# --- payload stored for the interface ---------------------------------------------------------


def _num(x: float, places: int = 4) -> float | None:
    """JSON has no infinity: an undefined ratio (no loss, no trade) is stored as null."""
    return round(x, places) if math.isfinite(x) else None


def trade_row(t: Trade) -> dict:
    return {
        "underlying": t.underlying,
        "strategy": t.strategy,
        "group": t.group,
        "sector": t.sector,
        "entry_day": t.entry_day.isoformat(),
        "expiration": t.expiration.isoformat() if t.expiration else None,
        "strikes": [k for k, _ in t.legs],
        "quantity": t.quantity,
        "credit": round(t.credit, 4),
        "short_delta": round(t.short_delta, 4),
        "pop": round(t.pop, 4),
        "iv_rank": None if t.iv_rank is None else round(t.iv_rank, 1),
        "exit_day": t.exit_day.isoformat() if t.exit_day else None,
        "exit_reason": t.exit_reason,
        "exit_price": None if t.exit_price is None else round(t.exit_price, 4),
        "pnl": round(t.pnl, 2),
        "days_held": t.days_held,
        "triggers": list(t.triggers),
    }


def weekly(points: Sequence[tuple[date, float, float]]) -> list[tuple[date, float, float]]:
    """Last point of each ISO week, plus the very first day: ~400 points for 8 years."""
    out: list[tuple[date, float, float]] = []
    for p in points:
        if out and out[-1][0].isocalendar()[:2] == p[0].isocalendar()[:2] and len(out) > 1:
            out[-1] = p
        else:
            out.append(p)
    return out


def yearly_returns(points: Sequence[tuple[date, float, float]], capital: float) -> list[dict]:
    """Calendar-year return of the account value; the first year starts from the capital."""
    rows, base, year, last = [], capital, None, capital
    for day, value, _ in points:
        if year is not None and day.year != year:
            rows.append({"year": year, "return": _num(last / base - 1)})
            base = last
        year, last = day.year, value
    if year is not None:
        rows.append({"year": year, "return": _num(last / base - 1)})
    return rows


def payload(result: BacktestResult, benchmark: Sequence[tuple[date, float]]) -> tuple[dict, dict]:
    """(summary, details) of a run, JSON-ready. `benchmark` is SPY's (day, close) series."""
    s = summarize(result)
    bench = {d: c for d, c in benchmark if s.start <= d <= s.end}
    days = sorted(bench)
    spy_final, spy_dd = (
        buy_and_hold([(d, bench[d]) for d in days], result.capital)
        if days
        else (result.capital, 0.0)
    )
    summary = {
        "start": s.start.isoformat(),
        "end": s.end.isoformat(),
        "capital": s.capital,
        "final": round(s.final, 2),
        "cagr": _num(s.cagr),
        "max_drawdown": _num(s.max_drawdown),
        "sharpe": _num(s.sharpe, 2),
        "trades": s.trades,
        "win_rate": _num(s.win_rate),
        "avg_win": round(s.avg_win, 2),
        "avg_loss": round(s.avg_loss, 2),
        "profit_factor": _num(s.profit_factor, 2),
        "avg_days_held": round(s.avg_days_held, 1),
        "avg_engaged_pct": _num(s.avg_engaged_pct),
        "days_with_deal_pct": _num(s.days_with_deal_pct),
        "open_at_end": sum(t.exit_day is None for t in result.trades),
        "benchmark_final": round(spy_final, 2),
        "benchmark_cagr": _num(cagr(result.capital, spy_final, (s.end - s.start).days)),
        "benchmark_drawdown": _num(spy_dd),
        "engine_version": ENGINE_VERSION,
        "blocked_days": result.blocked_days,
    }
    figures = summary_figures(result)
    summary.update({k: v for k, v in figures.items() if k not in summary})
    quality = data_quality(result)
    summary["data_quality"] = quality["level"]
    wheel = wheel_summary(result)
    summary["wheel_complete"] = True
    first = bench[days[0]] if days else None
    equity = [
        {
            "date": d.isoformat(),
            "equity": round(v, 2),
            "engaged": round(e, 2),
            "benchmark": (
                round(result.capital * bench[d] / first, 2) if first and d in bench else None
            ),
        }
        for d, v, e in weekly(result.equity)
    ]

    def rows(key: Callable[[Trade], object]) -> list[dict]:
        return [
            {
                "key": str(k),
                "trades": n,
                "win_rate": _num(w),
                "pnl": round(p, 2),
                "avg": round(a, 2),
            }
            for k, n, w, p, a in breakdown(result.trades, key)
        ]

    details = {
        "equity": equity,
        "yearly": yearly_returns(result.equity, result.capital),
        "breakdowns": {
            "exit_reason": rows(lambda t: t.exit_reason),
            "strategy": rows(lambda t: t.strategy),
            "group": rows(lambda t: t.group),
            "underlying": rows(lambda t: t.underlying),
            "year": rows(lambda t: t.entry_day.year),
            "entry_dte": rows(
                lambda t: (
                    f"{(t.expiration - t.entry_day).days // 5 * 5:03d}+"
                    if t.expiration
                    else "actions"
                )
            ),
        },
        "funnel": dict(result.funnel),
        "model": asdict(result.model),
        "periods": split_periods(result),
        "rolling": rolling_windows(result),
        "stress": stress_tests(result),
        "data_quality": quality,
        "wheel": wheel,
        "trades": [trade_row(t) for t in result.trades],
    }
    return summary, details
