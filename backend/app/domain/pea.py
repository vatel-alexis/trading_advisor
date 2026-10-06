"""PEA ETF portfolio: monthly trend-following rotation between PEA-eligible ETFs, in euros.

Each month-end, every equity ETF gets a momentum score (mean of its 1, 3, 6 and 12-month
returns). A level keeps its `top` best ETFs; one of them is only held while its trend is up
(score > 0 and price above its 10-month average), otherwise its share goes to the euro money
market ETF. The new target is traded at the next day's close.

History: the PEA ETFs are recent (2016-2019), so each one is extended back with the US ETF on
the same index converted to euros (monthly correlation 0.97-0.99 over the overlap, see
docs/pea.md). The money market ETF only tracks €STR since 2024-10; before, a yearly euro
short rate table stands in for it.

Standard library only: the worker computes the report, the API only reads it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from statistics import mean, pstdev

CASH = "cash"


@dataclass(frozen=True)
class Asset:
    key: str
    label: str
    name: str  # the PEA ETF to buy
    ticker: str  # Yahoo symbol of that ETF (Euronext Paris)
    isin: str | None
    proxy: str | None  # US ETF on the same index, priced in dollars
    since: date  # first day the PEA ETF's own prices are used


ASSETS: tuple[Asset, ...] = (
    Asset(
        "sp500",
        "S&P 500",
        "Amundi PEA S&P 500 UCITS ETF Acc",
        "PSP5.PA",
        "FR0011871128",
        "SPY",
        date(2016, 11, 1),
    ),
    Asset(
        "nasdaq",
        "Nasdaq-100",
        "Amundi PEA Nasdaq-100 UCITS ETF Acc",
        "PUST.PA",
        "FR0011871110",
        "QQQ",
        date(2016, 11, 1),
    ),
    Asset(
        "europe",
        "Europe",
        "Amundi PEA MSCI Europe UCITS ETF Acc",
        "PCEU.PA",
        None,
        "VGK",
        date(2019, 5, 1),
    ),
    Asset(
        "emerging",
        "Émergents",
        "Amundi PEA Émergent (MSCI Emerging) ESG Transition UCITS ETF Acc",
        "PAEEM.PA",
        "FR0013412020",
        "EEM",
        date(2019, 5, 1),
    ),
    Asset(
        "japan",
        "Japon",
        "Amundi PEA Japon (TOPIX) UCITS ETF Acc",
        "PTPXE.PA",
        None,
        "EWJ",
        date(2019, 5, 1),
    ),
    Asset(
        CASH,
        "Monétaire",
        "Amundi PEA Euro Court Terme UCITS ETF",
        "OBLI.PA",
        "FR0013346681",
        None,
        date(2024, 10, 1),
    ),
)
ASSET_BY_KEY = {a.key: a for a in ASSETS}
EQUITIES = tuple(a.key for a in ASSETS if a.key != CASH)
FX_TICKER = "EURUSD=X"  # dollars per euro

# Yearly mean of the euro overnight rate (EONIA, then €STR), in %, before the money market ETF
# tracked it; minus the ETF's 0.25 % fee. Approximate values, only used before 2024-10.
EURO_SHORT_RATE = {
    2005: 2.09, 2006: 2.83, 2007: 3.87, 2008: 3.87, 2009: 0.71, 2010: 0.44, 2011: 0.87,
    2012: 0.23, 2013: 0.09, 2014: 0.09, 2015: -0.11, 2016: -0.32, 2017: -0.35, 2018: -0.36,
    2019: -0.39, 2020: -0.46, 2021: -0.57, 2022: -0.01, 2023: 3.29, 2024: 3.65,
}  # fmt: skip
CASH_FEE = 0.25
TRADING_DAYS = 252

# Signal.
MOMENTUM_MONTHS = (1, 3, 6, 12)
TREND_MONTHS = 10
# Brokerage and spread, per euro bought or sold (Boursorama ~0.5 % on sales, buys often free).
COST = 0.003
# A month-end also rebalances to the target when a line drifted this far from it.
DRIFT = 0.05
BACKTEST_START = date(2007, 1, 1)
# Enough months before the backtest for the 12-month momentum.
DATA_START = date(2005, 1, 1)
PERIODS = (1, 3, 5, 10, 15)


@dataclass(frozen=True)
class Level:
    key: str
    label: str
    description: str
    top: int  # ETFs kept by the rotation
    rotation: float  # share of the portfolio in the rotation
    fixed: Mapping[str, float] = field(default_factory=dict)  # the rest, held all the time


LEVELS: tuple[Level, ...] = (
    Level(
        "securite",
        "Sécurité",
        "60 % en rotation entre les 3 meilleurs ETF, 40 % en monétaire en permanence.",
        3,
        0.6,
        {CASH: 0.4},
    ),
    Level("moyen", "Moyen", "100 % en rotation entre les 3 meilleurs ETF.", 3, 1.0),
    Level("dynamique", "Dynamique", "100 % en rotation entre les 2 meilleurs ETF.", 2, 1.0),
)
LEVEL_BY_KEY = {lv.key: lv for lv in LEVELS}


# --- euro index per asset --------------------------------------------------------------------


Series = Mapping[date, float]


def euro_indexes(prices: Mapping[str, Series], fx: Series) -> tuple[list[date], dict[str, list]]:
    """Daily index level per asset key, in euros, on the union of all trading days.

    Before an asset's PEA ETF is used (`since`, or its first price if later), its daily returns
    are the proxy's in euros; the money market uses the short rate table instead. A day
    without a price carries the previous one (no return); an index starts at 100 on its first
    price.
    """
    # Trading days of the ETFs only: the exchange rate also quotes on some days without trading.
    days = sorted({d for s in prices.values() for d in s if d >= DATA_START})
    fx_ff = _ffill(days, fx)
    out: dict[str, list] = {}
    for asset in ASSETS:
        own_prices = prices.get(asset.ticker) or {}
        own = _ffill(days, own_prices)
        switch = max(asset.since, min(own_prices, default=date.max))
        if asset.proxy:
            raw = _ffill(days, prices.get(asset.proxy, {}))
            proxy = [p / f if p and f else None for p, f in zip(raw, fx_ff, strict=True)]
        else:
            proxy = [None] * len(days)
        level: list[float | None] = []
        value: float | None = 100.0 if asset.key == CASH else None
        for i, d in enumerate(days):
            source = own if d >= switch else proxy
            if value is None:
                value = 100.0 if source[i] else None
            elif i:
                if d < switch and asset.key == CASH:
                    rate = EURO_SHORT_RATE.get(d.year, EURO_SHORT_RATE[max(EURO_SHORT_RATE)])
                    r = (rate - CASH_FEE) / 100 / TRADING_DAYS
                else:
                    r = _ret(source, i) or 0.0
                value *= 1 + r
            level.append(value)
        out[asset.key] = level
    return days, out


def _ffill(days: Sequence[date], series: Series) -> list[float | None]:
    out, last = [], None
    for d in days:
        last = series.get(d, last)
        out.append(last)
    return out


def _ret(values: Sequence[float | None], i: int) -> float | None:
    a, b = values[i - 1], values[i]
    return b / a - 1 if a and b else None


# --- signal ----------------------------------------------------------------------------------


def month_ends(days: Sequence[date]) -> list[int]:
    """Index of the last day of each calendar month (the last month may be incomplete)."""
    return [i for i in range(len(days)) if i + 1 == len(days) or days[i + 1].month != days[i].month]


@dataclass(frozen=True)
class Score:
    key: str
    momentum: float | None
    trend_ok: bool
    price: float | None
    average: float | None


def scores(index: Mapping[str, Sequence[float | None]], ends: Sequence[int]) -> list[Score]:
    """Momentum and trend of each equity ETF at the last of `ends` (month-end closes)."""
    out = []
    for key in EQUITIES:
        closes = [index[key][i] for i in ends]
        need = max(*MOMENTUM_MONTHS, TREND_MONTHS) + 1
        if len(closes) < need or any(c is None for c in closes[-need:]):
            out.append(Score(key, None, False, closes[-1] if closes else None, None))
            continue
        now = closes[-1]
        momentum = mean(now / closes[-1 - k] - 1 for k in MOMENTUM_MONTHS)
        average = mean(closes[-TREND_MONTHS:])
        out.append(Score(key, momentum, momentum > 0 and now > average, now, average))
    return out


def target(level: Level, ranked: Sequence[Score]) -> dict[str, float]:
    """Target weights of a level from the month's scores (keys with weight > 0 only)."""
    weights: dict[str, float] = {}
    usable = sorted((s for s in ranked if s.momentum is not None), key=lambda s: -s.momentum)
    slot = level.rotation / level.top
    for i in range(level.top):
        pick = usable[i] if i < len(usable) else None
        key = pick.key if pick and pick.trend_ok else CASH
        weights[key] = weights.get(key, 0.0) + slot
    for key, w in level.fixed.items():
        weights[key] = weights.get(key, 0.0) + w
    return {k: round(w, 6) for k, w in weights.items() if w > 1e-9}


# --- backtest --------------------------------------------------------------------------------


@dataclass
class Adjustment:
    day: date  # traded at this day's close
    signal_day: date
    before: dict[str, float]
    after: dict[str, float]
    turnover: float
    reason: str  # "signal" (target changed) or "drift"


@dataclass
class Backtest:
    days: list[date]
    values: list[float]
    adjustments: list[Adjustment]
    exposure: dict[str, float]  # mean weight per asset over the period
    targets: list[tuple[date, dict[str, float]]]  # month-end signals


def backtest(
    level: Level,
    days: Sequence[date],
    index: Mapping[str, Sequence[float | None]],
    start: date = BACKTEST_START,
) -> Backtest:
    """Daily value of 1.0 invested at `start`; month-end signal, traded the next day."""
    first = next(i for i, d in enumerate(days) if d >= start)
    ends = month_ends(days)
    end_set = set(ends)
    weights: dict[str, float] = {}
    current_target: dict[str, float] = {}
    pending: tuple[date, dict[str, float], str] | None = None
    values = [1.0]
    adjustments: list[Adjustment] = []
    targets: list[tuple[date, dict[str, float]]] = []
    exposure = dict.fromkeys(ASSET_BY_KEY, 0.0)
    # The first target comes from the month-end before `start`.
    prior = [i for i in ends if i < first]
    if prior:
        current_target = target(level, scores(index, prior))
        pending = (days[prior[-1]], current_target, "signal")
    for i in range(first + 1, len(days)):
        growth = 0.0
        new: dict[str, float] = {}
        for k, w in weights.items():
            r = _ret(index[k], i) or 0.0
            growth += w * r
            new[k] = w * (1 + r)
        total = sum(new.values())
        weights = {k: v / total for k, v in new.items()} if total > 0 else new
        value = values[-1] * (1 + growth)
        if pending:
            signal_day, goal, reason = pending
            pending = None
            keys = set(goal) | set(weights)
            turnover = sum(abs(goal.get(k, 0.0) - weights.get(k, 0.0)) for k in keys)
            value *= 1 - COST * turnover
            adjustments.append(
                Adjustment(days[i], signal_day, _rounded(weights), dict(goal), turnover, reason)
            )
            weights = dict(goal)
        values.append(value)
        for k, w in weights.items():
            exposure[k] += w
        if i in end_set and i + 1 < len(days):
            goal = target(level, scores(index, [e for e in ends if e <= i]))
            targets.append((days[i], goal))
            keys = set(goal) | set(current_target) | set(weights)
            if any(abs(goal.get(k, 0.0) - current_target.get(k, 0.0)) > 1e-6 for k in keys):
                pending = (days[i], goal, "signal")
            elif any(abs(weights.get(k, 0.0) - goal.get(k, 0.0)) > DRIFT for k in keys):
                pending = (days[i], goal, "drift")
            current_target = goal
    n = max(len(values) - 1, 1)
    return Backtest(
        list(days[first:]),
        values,
        adjustments,
        {k: v / n for k, v in exposure.items() if v > 0},
        targets,
    )


def _rounded(weights: Mapping[str, float]) -> dict[str, float]:
    return {k: round(w, 4) for k, w in weights.items() if w > 1e-6}


# --- statistics ------------------------------------------------------------------------------


def annualized(days: Sequence[date], values: Sequence[float], years: float) -> float | None:
    """Annual return over the last `years` (None when the history is shorter)."""
    end = days[-1]
    try:
        cut = end.replace(year=end.year - int(years))
    except ValueError:  # 29 February
        cut = end.replace(year=end.year - int(years), day=28)
    if days[0] > cut:
        return None
    i = next(k for k, d in enumerate(days) if d >= cut)
    span = (end - days[i]).days / 365.25
    return (values[-1] / values[i]) ** (1 / span) - 1 if span > 0 else None


def summary(bt: Backtest) -> dict[str, object]:
    days, values = bt.days, bt.values
    years = (days[-1] - days[0]).days / 365.25
    peak, drawdown = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        drawdown = max(drawdown, 1 - v / peak)
    daily = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    # Index days without a return on either calendar would dilute the volatility.
    daily = [r for r in daily if r != 0.0]
    yearly: dict[int, float] = {}
    first_of_year = values[0]
    for i, d in enumerate(days):
        if i + 1 == len(days) or days[i + 1].year != d.year:
            yearly[d.year] = values[i] / first_of_year - 1
            first_of_year = values[i]
    signal_changes = [a for a in bt.adjustments[1:] if a.reason == "signal"]
    return {
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "years": round(years, 2),
        "cagr": (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 else None,
        "total_return": values[-1] / values[0] - 1,
        "periods": {str(y): annualized(days, values, y) for y in PERIODS},
        "max_drawdown": drawdown,
        "volatility": pstdev(daily) * TRADING_DAYS**0.5 if len(daily) > 1 else None,
        "yearly": {str(y): r for y, r in yearly.items()},
        "worst_year": min(yearly.items(), key=lambda kv: kv[1]) if yearly else None,
        # The first trade only invests the starting cash.
        "adjustments": len(bt.adjustments) - 1,
        "adjustments_per_year": (len(bt.adjustments) - 1) / years if years > 0 else None,
        "signal_changes": len(signal_changes),
        "exposure": {k: round(w, 4) for k, w in sorted(bt.exposure.items(), key=lambda kv: -kv[1])},
    }


def monthly_curve(days: Sequence[date], values: Sequence[float]) -> list[dict[str, object]]:
    ends = month_ends(days)
    return [
        {"date": days[i].isoformat(), "value": round(values[i] / values[0] * 100, 2)} for i in ends
    ]


# --- report ----------------------------------------------------------------------------------


def changes(before: Mapping[str, float], after: Mapping[str, float]) -> list[dict[str, object]]:
    """What to buy and sell to go from one allocation to the other (points of the portfolio)."""
    out = []
    for key in sorted(set(before) | set(after), key=lambda k: list(ASSET_BY_KEY).index(k)):
        a, b = before.get(key, 0.0), after.get(key, 0.0)
        if abs(b - a) > 0.005:
            out.append(
                {
                    "asset": key,
                    "from": round(a, 4),
                    "to": round(b, 4),
                    "action": "acheter" if b > a else "vendre",
                }
            )
    return out


def report(
    days: Sequence[date], index: Mapping[str, Sequence[float | None]], today: date
) -> dict[str, object]:
    """Everything the PEA page shows, as JSON-ready data."""
    ends = month_ends(days)
    last_day = days[-1]
    # The current month is complete only on its last trading day; otherwise the latest signal
    # is the previous month-end's and today's prices give a provisional one.
    complete = [i for i in ends if days[i].month != today.month or days[i].year != today.year]
    in_progress = ends[-1] not in complete
    signal_ends = complete if in_progress else ends
    signal_day = days[signal_ends[-1]]
    ranked = scores(index, signal_ends)
    previous_ranked = scores(index, signal_ends[:-1])
    preview_ranked = scores(index, ends) if in_progress else None

    benchmark_level = Level("benchmark", "S&P 500", "", 1, 0.0, {"sp500": 1.0})
    bench = backtest(benchmark_level, days, index)
    levels = []
    for level in LEVELS:
        bt = backtest(level, days, index)
        stats = summary(bt)
        current = target(level, ranked)
        previous = target(level, previous_ranked)
        preview = target(level, preview_ranked) if preview_ranked else None
        levels.append(
            {
                "key": level.key,
                "label": level.label,
                "description": level.description,
                "target": current,
                "previous": previous,
                "changes": changes(previous, current),
                "preview": preview,
                "preview_changes": changes(current, preview) if preview is not None else [],
                "summary": stats,
                "curve": monthly_curve(bt.days, bt.values),
                "history": [
                    {
                        "day": a.day.isoformat(),
                        "signal_day": a.signal_day.isoformat(),
                        "reason": a.reason,
                        "changes": changes(a.before, a.after),
                    }
                    for a in bt.adjustments[1:]
                ][-24:][::-1],
            }
        )
    return {
        "as_of": last_day.isoformat(),
        "signal_day": signal_day.isoformat(),
        "next_signal": "dernier jour de bourse du mois",
        "provisional_day": last_day.isoformat() if in_progress else None,
        "scores": [
            {
                "asset": s.key,
                "momentum": s.momentum,
                "trend_ok": s.trend_ok,
                "price": s.price,
                "average": s.average,
            }
            for s in ranked
        ],
        "assets": [
            {
                "key": a.key,
                "label": a.label,
                "name": a.name,
                "ticker": a.ticker.removesuffix(".PA"),
                "isin": a.isin,
                "proxy": a.proxy,
                "since": a.since.isoformat(),
            }
            for a in ASSETS
        ],
        "levels": levels,
        "benchmark": {"label": "S&P 500 (PEA) conservé", **summary(bench)},
        "benchmark_curve": monthly_curve(bench.days, bench.values),
        "assumptions": {
            "cost": COST,
            "drift": DRIFT,
            "momentum_months": list(MOMENTUM_MONTHS),
            "trend_months": TREND_MONTHS,
            "start": BACKTEST_START.isoformat(),
        },
    }
