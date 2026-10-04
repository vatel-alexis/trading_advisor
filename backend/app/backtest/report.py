"""Performance figures of a backtest run, and their rendering as Markdown and CSV."""

import csv
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.backtest.engine import BacktestResult, Trade


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
        profit_factor=sum(wins) / -sum(losses) if sum(losses) < 0 else math.inf,
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
