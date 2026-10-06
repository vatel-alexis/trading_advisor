"""Day-by-day simulation: the live screener picks the entries, the live exit rules close them.

Each trading day, at the close: open positions are marked and closed when an exit rule fires,
then the screener runs on the reconstructed chains and every selected deal is filled (the
backtest accepts all proposals that Alexis would validate by hand), unless the account's loss
limits block new entries that day, as the live entry gate does.

The True Wheel is simulated to the end: a cash-secured put that expires in the money is
assigned, the shares are held and marked every day, covered calls are sold on them, and the
shares go when a call expires in the money. The total P&L counts puts, calls and shares.

Prices are reconstructed (Black-Scholes on a volatility proxy), not historical quotes.
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
    option_mid,
    put_iv,
    snapshot,
)
from app.domain.exits import (
    PROFIT_TARGET,
    STOP_LOSS,
    MarketView,
    ShortPremium,
    evaluate_exit,
    stop_price,
    take_profit_price,
)
from app.domain.params import StrategyParams
from app.domain.pricing import bs_delta
from app.domain.risk import AccountState, Exposure, stress_move
from app.domain.screener import COVERED_CALL, PUT_CREDIT_SPREAD, ShareLot, screen

IV_HISTORY_DAYS = 252
CALENDAR_SYMBOL = "SPY"  # trading days of the simulation
PROGRESS_EVERY = 20  # trading days between two progress reports
# Exit reasons of the wheel, besides the exit rules.
EXPIRATION = "expiration"
ASSIGNMENT = "assignment"
CALLED_AWAY = "called_away"
SHARES = "shares"  # strategy of a share lot received on assignment


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
    option_type: str = "put"
    # Half bid/ask spreads per share of the strategy at entry and at a stop or time exit (0 for
    # a resting target or an expiration): what the slippage stress tests scale.
    entry_spread: float = 0.0
    exit_spread: float = 0.0
    mark: float = 0.0
    exit_day: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    # Stop signals that closed the trade (cost, delta, breach, event, portfolio...).
    triggers: tuple[str, ...] = ()

    @property
    def width(self) -> float | None:
        strikes = [k for k, _ in self.legs]
        return max(strikes) - min(strikes) if len(strikes) > 1 else None

    @property
    def short_strike(self) -> float:
        return next(k for k, sign in self.legs if sign > 0)

    @property
    def pnl(self) -> float:
        close = self.exit_price if self.exit_price is not None else self.mark
        return (self.credit - close) * 100 * self.quantity

    @property
    def days_held(self) -> int:
        return ((self.exit_day or self.entry_day) - self.entry_day).days


@dataclass
class Holding:
    """Shares received on a True Wheel assignment, in split-adjusted units."""

    underlying: str
    group: str
    sector: str | None
    entry_day: date
    shares_adj: float
    entry_adj: float  # adjusted close of the assignment day
    # Cost basis net of the put premium (adjusted): covered calls are sold at or above it.
    basis_adj: float
    mark_adj: float = 0.0
    exit_day: date | None = None
    exit_adj: float | None = None
    exit_reason: str | None = None
    # Same reading as a trade for the reports.
    strategy: str = SHARES
    expiration: date | None = None
    legs: list = field(default_factory=list)
    credit: float = 0.0
    short_delta: float = 0.0
    pop: float = 0.0
    iv_rank: float | None = None
    exit_price: float | None = None
    triggers: tuple[str, ...] = ()
    entry_spread: float = 0.0
    exit_spread: float = 0.0

    @property
    def value(self) -> float:
        return self.shares_adj * self.mark_adj

    @property
    def cost(self) -> float:
        return self.shares_adj * self.entry_adj

    @property
    def quantity(self) -> int:
        return round(self.shares_adj / 100)

    @property
    def pnl(self) -> float:
        close = self.exit_adj if self.exit_adj is not None else self.mark_adj
        return self.shares_adj * (close - self.entry_adj)

    @property
    def days_held(self) -> int:
        return ((self.exit_day or self.entry_day) - self.entry_day).days


@dataclass
class BacktestResult:
    params: StrategyParams
    model: ModelConfig
    capital: float
    trades: list
    # (day, account value with open positions marked at mid, collateral engaged)
    equity: list[tuple[date, float, float]]
    funnel: Counter = field(default_factory=Counter)
    days_with_deal: int = 0
    # (day, sum of the open positions' max losses, sum of their stress losses)
    open_risk: list[tuple[date, float, float]] = field(default_factory=list)
    # Days on which the loss limits blocked new entries.
    blocked_days: int = 0


@dataclass(frozen=True)
class ExitContext:
    """What the live monitor would also know: the drawdown limit and the position it hits."""

    portfolio_breach: bool = False


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
        mid = option_mid(t.option_type, spot, strike, years, atm, skew, model.rate)
        mark += sign * mid
        spreads += half_spread(mid, s.half_spread_pct, model)
    return max(0.0, mark), spreads


def _view(
    t: Trade,
    s: SymbolSeries,
    i: int,
    mark: float,
    spreads: float,
    params: StrategyParams,
    model: ModelConfig,
    context: ExitContext,
) -> MarketView:
    """The live monitor's view at the close. A daily close counts as a confirmed mark."""
    day = s.history.dates[i]
    spot = s.history.closes[i] * t.split_factor
    strike = t.short_strike
    years = max((t.expiration - day).days, 0) / 365
    atm = s.atm_iv[i] or model.min_atm_iv
    skew = model.skew_etf if s.is_etf else model.skew_stock
    iv = put_iv(spot, strike, years, atm, skew) if years > 0 else atm
    delta = bs_delta(t.option_type, spot, strike, years, iv, model.rate)
    return MarketView(
        natural=mark + spreads,
        confirmations=params.stop_confirmations,
        short_delta=delta,
        spot=spot,
        short_strike=strike,
        next_event=s.next_earnings(day),
        portfolio_breach=context.portfolio_breach,
    )


def exit_fill(
    t: Trade,
    s: SymbolSeries,
    i: int,
    params: StrategyParams,
    model: ModelConfig,
    context: ExitContext | None = None,
) -> tuple[str, float] | None:
    """Exit reason and fill price per share on day i, or None to hold.

    The live monitor checks every few minutes, so the stop and the target are tested on the
    day's low and high, not only on the close: the stop fills where the price crossed it (or at
    the open after a gap), plus the natural spread; the resting target fills at its limit.
    The stop is tested first. With a `context`, the other stop signals (delta, underlying
    through the strike, earnings, drawdown limit) are tested on the close like the time exit,
    and fill at the close plus the slippage. At expiration, an in-the-money True Wheel put is
    assigned and an in-the-money covered call takes the shares away.
    Sets the trade's `exit_spread` and `triggers`.
    """
    h = s.history
    close = h.closes[i]
    low = min(h.lows[i], close) if h.lows else close
    high = max(h.highs[i], close) if h.highs else close
    # A sold put loses on the day's low, a covered call on its high.
    call = t.option_type == "call"
    adverse, favorable = (high, low) if call else (low, high)
    position = ShortPremium(
        t.strategy, t.credit, t.expiration, assignment_accepted=t.strategy == "cash_secured_put"
    )
    day = h.dates[i]

    worst, spreads = _price(t, s, i, adverse, model)
    signal = evaluate_exit(position, worst, day, params)
    if signal is not None and signal.reason == STOP_LOSS:
        at_open, _ = _price(t, s, i, h.opens[i], model) if h.opens else (worst, 0.0)
        trigger = max(stop_price(t.credit, params), min(at_open, worst))
        t.exit_spread, t.triggers = spreads, signal.triggers
        return STOP_LOSS, _cap(t, trigger + model.exit_slippage * spreads)

    best, _ = _price(t, s, i, favorable, model)
    signal = evaluate_exit(position, best, day, params)
    if signal is not None and signal.reason == PROFIT_TARGET:
        return PROFIT_TARGET, take_profit_price(t.credit, params)

    if t.expiration <= day:
        spot = close * t.split_factor
        strike = t.short_strike
        intrinsic = max(0.0, spot - strike) if call else max(0.0, strike - spot)
        if t.strategy == "cash_secured_put" and intrinsic > 0:
            return ASSIGNMENT, intrinsic
        if t.strategy == COVERED_CALL and intrinsic > 0:
            return CALLED_AWAY, intrinsic
        mark, _ = _price(t, s, i, close, model)
        return EXPIRATION, _cap(t, mark)

    mark, spreads = _price(t, s, i, close, model)
    view = _view(t, s, i, mark, spreads, params, model, context) if context else None
    signal = evaluate_exit(position, mark, day, params, view)
    if signal is not None:
        t.exit_spread, t.triggers = spreads, signal.triggers
        return signal.reason, _cap(t, mark + model.exit_slippage * spreads)
    return None


def _cap(t: Trade, price: float) -> float:
    """A spread never costs more than its width to close."""
    return min(price, t.width) if t.width is not None else price


def _lot_stress(lot: Holding, params: StrategyParams) -> float:
    return lot.cost * stress_move(lot.underlying, lot.sector, params)


@dataclass
class _Limits:
    """Account values the loss limits compare with, from the end-of-day curve."""

    peak: float
    previous: float
    month_start: float
    month: tuple[int, int] | None = None

    def blocked(self, value: float, day: date, params: StrategyParams) -> bool:
        if self.month != (day.year, day.month):
            self.month, self.month_start = (day.year, day.month), self.previous
        daily = value / self.previous - 1 if self.previous > 0 else 0.0
        monthly = value / self.month_start - 1 if self.month_start > 0 else 0.0
        drawdown = (self.peak - value) / self.peak if self.peak > 0 else 0.0
        return (
            -daily >= params.max_daily_loss_pct
            or -monthly >= params.max_monthly_loss_pct
            or drawdown >= params.max_drawdown_pct
        )

    def close_day(self, value: float) -> None:
        self.previous = value
        self.peak = max(self.peak, value)


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
    holdings: list[Holding] = []
    closed: list = []
    equity: list[tuple[date, float, float]] = []
    open_risk: list[tuple[date, float, float]] = []
    funnel: Counter = Counter()
    days_with_deal = 0
    blocked_days = 0
    last_exit: dict[str, date] = {}
    limits = _Limits(capital, capital, capital)

    for n, day in enumerate(calendar):
        if progress is not None and n % PROGRESS_EVERY == 0:
            progress(n / len(calendar))
        index = {sym: s.index_of(day) for sym, s in series.items()}

        # The drawdown limit stops the position losing the most (from yesterday's marks).
        worst = None
        last_value = equity[-1][1] if equity else capital
        if limits.peak > 0 and (limits.peak - last_value) / limits.peak >= params.max_drawdown_pct:
            losing = [t for t in open_trades if t.mark > t.credit]
            if losing:
                worst = max(losing, key=lambda t: (t.mark - t.credit) * t.quantity)

        # 1. Exits during the day.
        still_open = []
        for t in open_trades:
            i = index.get(t.underlying)
            if i is None:
                still_open.append(t)
                continue
            s = series[t.underlying]
            t.mark, _ = _price(t, s, i, s.history.closes[i], model)
            context = ExitContext(portfolio_breach=t is worst)
            fill = exit_fill(t, s, i, params, model, context)
            if fill is None:
                still_open.append(t)
                continue
            t.exit_day = day
            t.exit_reason, t.exit_price = fill
            last_exit[t.underlying] = day
            realized += t.pnl
            closed.append(t)
            if t.exit_reason == ASSIGNMENT:
                holdings.append(
                    Holding(
                        underlying=t.underlying,
                        group=t.group,
                        sector=t.sector,
                        entry_day=day,
                        # Contracts follow splits: 100 shares per contract in entry units.
                        shares_adj=100 * t.quantity * t.split_factor,
                        entry_adj=s.history.closes[i],
                        basis_adj=(t.short_strike - t.credit) / t.split_factor,
                        mark_adj=s.history.closes[i],
                    )
                )
            elif t.exit_reason == CALLED_AWAY:
                lot = next((h for h in holdings if h.underlying == t.underlying), None)
                if lot is not None:
                    lot.exit_day, lot.exit_adj, lot.exit_reason = (
                        day,
                        s.history.closes[i],
                        CALLED_AWAY,
                    )
                    realized += lot.pnl
                    holdings.remove(lot)
                    closed.append(lot)
        open_trades = still_open
        for lot in holdings:
            i = index.get(lot.underlying)
            if i is not None:
                lot.mark_adj = series[lot.underlying].history.closes[i]

        # 2. Screen today's chains and fill the selected deals, unless the loss limits block
        #    new risk (covered calls only add premium to shares held: still sold).
        unrealized = sum((t.credit - t.mark) * 100 * t.quantity for t in open_trades)
        value = capital + realized + unrealized + sum(lot.pnl for lot in holdings)
        blocked = limits.blocked(value, day, params)
        blocked_days += blocked
        covered = {t.underlying for t in open_trades if t.strategy == COVERED_CALL}
        uncovered = {lot.underlying: lot for lot in holdings if lot.underlying not in covered}
        snaps, iv_history, lots = [], {}, []
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
                calls=sym in uncovered,
            )
            if snap is None:
                continue
            snaps.append(snap)
            iv_history[sym] = [v for v in s.atm_iv[max(0, i - IV_HISTORY_DAYS) : i] if v]
            if sym in uncovered:
                lot = uncovered[sym]
                factor = s.history.split_factors[i]
                lots.append(ShareLot(sym, int(lot.shares_adj / factor), lot.basis_adj * factor))
        exposures = [
            Exposure(
                t.underlying, t.sector, t.strategy, t.max_loss, t.stress_loss, t.collateral,
                t.expiration,
            )
            for t in open_trades
        ] + [
            Exposure(
                lot.underlying, lot.sector, None, lot.cost, _lot_stress(lot, params), lot.cost,
                None,
            )
            for lot in holdings
        ]  # fmt: skip
        engaged = sum(e.collateral for e in exposures)
        account = AccountState(capital + realized, engaged)
        result = screen(
            snaps,
            day,
            params,
            account,
            iv_history,
            share_lots=lots,
            open_underlyings={t.underlying for t in open_trades}
            | {lot.underlying for lot in holdings},
            exposures=exposures,
            cooling_down={
                sym
                for sym, exited in last_exit.items()
                if (day - exited).days < params.reentry_cooldown_days
            },
        )
        funnel.update(result.funnel)
        selected = [] if blocked else list(result.selected)
        if blocked and result.selected:
            funnel["loss_limits"] += len(result.selected)
        if selected:
            days_with_deal += 1
        for c in [*selected, *result.covered_calls]:
            spreads = sum((leg.quote.ask - leg.quote.bid) / 2 for leg in c.legs)
            credit = c.credit - model.entry_slippage * spreads
            if credit <= 0 or c.quantity <= 0:
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
                option_type=c.legs[0].quote.option_type,
                entry_spread=spreads,
                mark=credit,
            )
            if c.strategy == PUT_CREDIT_SPREAD:
                # Max loss follows the filled credit, not the mid.
                t.max_loss = ((t.width or 0) - credit) * 100 * t.quantity
            open_trades.append(t)

        unrealized = sum((t.credit - t.mark) * 100 * t.quantity for t in open_trades)
        shares = sum(lot.pnl for lot in holdings)
        engaged = sum(t.collateral for t in open_trades) + sum(lot.cost for lot in holdings)
        value = capital + realized + unrealized + shares
        equity.append((day, value, engaged))
        limits.close_day(value)
        open_risk.append(
            (
                day,
                sum(t.max_loss for t in open_trades) + sum(lot.cost for lot in holdings),
                sum(t.stress_loss for t in open_trades)
                + sum(_lot_stress(lot, params) for lot in holdings),
            )
        )

    if progress is not None:
        progress(1.0)
    return BacktestResult(
        params,
        model,
        capital,
        closed + open_trades + holdings,
        equity,
        funnel,
        days_with_deal,
        open_risk,
        blocked_days,
    )
