"""Day-by-day simulation: the live screener picks the entries, the live exit rules close them.

Each trading day, at the close: open positions are marked and closed when an exit rule fires,
then the screener runs on the reconstructed chains and every selected deal is filled (the
backtest accepts all proposals that Alexis would validate by hand).
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from app.backtest.data import MarketHistory
from app.backtest.synth import (
    ModelConfig,
    SymbolSeries,
    build_series,
    half_spread,
    put_mid,
    snapshot,
)
from app.domain.exits import (
    PROFIT_TARGET,
    STOP_LOSS,
    ShortPremium,
    evaluate_exit,
    stop_price,
    take_profit_price,
)
from app.domain.params import StrategyParams
from app.domain.risk import AccountState, Exposure
from app.domain.screener import PUT_CREDIT_SPREAD, screen

IV_HISTORY_DAYS = 252
CALENDAR_SYMBOL = "SPY"  # trading days of the simulation
PROGRESS_EVERY = 20  # trading days between two progress reports


@dataclass
class Trade:
    underlying: str
    strategy: str
    group: str
    sector: str | None
    entry_day: date
    expiration: date
    quantity: int
    # (strike, +1 sold / -1 bought), in the prices of the entry day (before later splits).
    legs: list[tuple[float, int]]
    # Per share, after slippage.
    credit: float
    collateral: float  # total
    max_loss: float  # total
    stress_loss: float  # total
    short_delta: float
    pop: float
    iv_rank: float | None
    # Split factor of the entry day: the trade is priced in that day's share units.
    split_factor: float
    mark: float = 0.0
    exit_day: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    @property
    def width(self) -> float | None:
        strikes = [k for k, _ in self.legs]
        return max(strikes) - min(strikes) if len(strikes) > 1 else None

    @property
    def pnl(self) -> float:
        close = self.exit_price if self.exit_price is not None else self.mark
        return (self.credit - close) * 100 * self.quantity

    @property
    def days_held(self) -> int:
        return ((self.exit_day or self.entry_day) - self.entry_day).days


@dataclass
class BacktestResult:
    params: StrategyParams
    model: ModelConfig
    capital: float
    trades: list[Trade]
    # (day, account value with open positions marked at mid, collateral engaged)
    equity: list[tuple[date, float, float]]
    funnel: Counter = field(default_factory=Counter)
    days_with_deal: int = 0


def _price(
    t: Trade, s: SymbolSeries, i: int, spot_adj: float, model: ModelConfig
) -> tuple[float, float]:
    """Cost to close at mid per share for a split-adjusted spot, and the legs' half spreads.

    Intraday prices use the day's closing volatility, which is the higher one on a sell-off.
    """
    day = s.history.dates[i]
    spot = spot_adj * t.split_factor
    atm = s.atm_iv[i] or model.min_atm_iv
    skew = model.skew_etf if s.is_etf else model.skew_stock
    years = (t.expiration - day).days / 365
    mark, spreads = 0.0, 0.0
    for strike, sign in t.legs:
        mid = (
            put_mid(spot, strike, years, atm, skew, model.rate)
            if years > 0
            else max(0.0, strike - spot)
        )
        mark += sign * mid
        spreads += half_spread(mid, s.half_spread_pct, model)
    return max(0.0, mark), spreads


def exit_fill(
    t: Trade, s: SymbolSeries, i: int, params: StrategyParams, model: ModelConfig
) -> tuple[str, float] | None:
    """Exit reason and fill price per share on day i, or None to hold.

    The live monitor checks every few minutes, so the stop and the target are tested on the
    day's low and high, not only on the close: the stop fills where the price crossed it (or at
    the open after a gap), plus the natural spread; the resting target fills at its limit.
    The stop is tested first. The time exit is decided on the close, at the natural price.
    """
    h = s.history
    close = h.closes[i]
    low = min(h.lows[i], close) if h.lows else close
    high = max(h.highs[i], close) if h.highs else close
    position = ShortPremium(
        t.strategy, t.credit, t.expiration, assignment_accepted=t.strategy == "cash_secured_put"
    )
    day = h.dates[i]

    worst, spreads = _price(t, s, i, low, model)
    signal = evaluate_exit(position, worst, day, params)
    if signal is not None and signal.reason == STOP_LOSS:
        at_open, _ = _price(t, s, i, h.opens[i], model) if h.opens else (worst, 0.0)
        trigger = max(stop_price(t.credit, params), min(at_open, worst))
        return STOP_LOSS, _cap(t, trigger + model.exit_slippage * spreads)

    best, _ = _price(t, s, i, high, model)
    signal = evaluate_exit(position, best, day, params)
    if signal is not None and signal.reason == PROFIT_TARGET:
        return PROFIT_TARGET, take_profit_price(t.credit, params)

    mark, spreads = _price(t, s, i, close, model)
    signal = evaluate_exit(position, mark, day, params)
    if signal is not None:
        return signal.reason, _cap(t, mark + model.exit_slippage * spreads)
    if t.expiration <= day:
        return "expiration", _cap(t, mark)
    return None


def _cap(t: Trade, price: float) -> float:
    """A spread never costs more than its width to close."""
    return min(price, t.width) if t.width is not None else price


def run_backtest(
    market: MarketHistory,
    params: StrategyParams,
    start: date,
    end: date,
    capital: float = 20_000.0,
    model: ModelConfig | None = None,
    progress: Callable[[float], None] | None = None,
) -> BacktestResult:
    """Simulate `params` from `start` to `end`; `progress` gets the share of days done."""
    model = model or ModelConfig()
    series = build_series(market, params, model)
    calendar = [d for d in market.symbols[CALENDAR_SYMBOL].dates if start <= d <= end]
    lowest_width = max(params.spread_widths, default=0.0)

    realized = 0.0
    open_trades: list[Trade] = []
    closed: list[Trade] = []
    equity: list[tuple[date, float, float]] = []
    funnel: Counter = Counter()
    days_with_deal = 0
    last_exit: dict[str, date] = {}

    for n, day in enumerate(calendar):
        if progress is not None and n % PROGRESS_EVERY == 0:
            progress(n / len(calendar))
        index = {sym: s.index_of(day) for sym, s in series.items()}

        # 1. Exits during the day.
        still_open = []
        for t in open_trades:
            i = index.get(t.underlying)
            if i is None:
                still_open.append(t)
                continue
            s = series[t.underlying]
            t.mark, _ = _price(t, s, i, s.history.closes[i], model)
            fill = exit_fill(t, s, i, params, model)
            if fill is None:
                still_open.append(t)
                continue
            t.exit_day = day
            t.exit_reason, t.exit_price = fill
            last_exit[t.underlying] = day
            realized += t.pnl
            closed.append(t)
        open_trades = still_open

        # 2. Screen today's chains and fill the selected deals.
        snaps, iv_history = [], {}
        for sym in params.universe:
            s, i = series.get(sym), index.get(sym)
            if s is None or i is None:
                continue
            group = params.group_of(sym)
            sector = "ETF" if group == "etf" else s.history.sector
            snap = snapshot(
                s,
                i,
                sector,
                params.dte_min,
                params.dte_max,
                group in ("short_put", "true_wheel"),
                lowest_width,
                model,
            )
            if snap is None:
                continue
            snaps.append(snap)
            iv_history[sym] = [v for v in s.atm_iv[max(0, i - IV_HISTORY_DAYS) : i] if v]
        engaged = sum(t.collateral for t in open_trades)
        account = AccountState(capital + realized, engaged)
        result = screen(
            snaps,
            day,
            params,
            account,
            iv_history,
            open_underlyings={t.underlying for t in open_trades},
            exposures=[
                Exposure(
                    t.underlying,
                    t.sector,
                    t.strategy,
                    t.max_loss,
                    t.stress_loss,
                    t.collateral,
                    t.expiration,
                )
                for t in open_trades
            ],
            cooling_down={
                sym
                for sym, exited in last_exit.items()
                if (day - exited).days < params.reentry_cooldown_days
            },
        )
        funnel.update(result.funnel)
        if result.selected:
            days_with_deal += 1
        for c in result.selected:
            spreads = sum((leg.quote.ask - leg.quote.bid) / 2 for leg in c.legs)
            credit = c.credit - model.entry_slippage * spreads
            if credit <= 0:
                continue
            i = index[c.underlying]
            t = Trade(
                underlying=c.underlying,
                strategy=c.strategy,
                group=c.group,
                sector=c.sector,
                entry_day=day,
                expiration=c.expiration,
                quantity=c.quantity,
                legs=[(leg.quote.strike, 1 if leg.side == "sell" else -1) for leg in c.legs],
                credit=credit,
                collateral=c.collateral * c.quantity,
                max_loss=c.max_loss * c.quantity,
                stress_loss=c.stress_loss * c.quantity,
                short_delta=c.short_delta,
                pop=c.pop,
                iv_rank=c.iv_rank.value if c.iv_rank else None,
                split_factor=series[c.underlying].history.split_factors[i],
                mark=credit,
            )
            if c.strategy == PUT_CREDIT_SPREAD:
                # Max loss follows the filled credit, not the mid.
                t.max_loss = ((t.width or 0) - credit) * 100 * t.quantity
            open_trades.append(t)

        unrealized = sum((t.credit - t.mark) * 100 * t.quantity for t in open_trades)
        engaged = sum(t.collateral for t in open_trades)
        equity.append((day, capital + realized + unrealized, engaged))

    if progress is not None:
        progress(1.0)
    return BacktestResult(
        params, model, capital, closed + open_trades, equity, funnel, days_with_deal
    )
