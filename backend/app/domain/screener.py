"""Daily screener: filters option chains, builds trades, ranks them and picks the day's deals.

New entries are put credit spreads (index ETFs; large caps in the experimental mode), Short
Put Income puts (sold, bought back at 21 DTE) and True Wheel puts (assignment accepted, only on
stocks the user accepts to own). Covered calls are proposed separately for shares already held
after an assignment: they need no new capital and do not count toward the daily deals.
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.domain.market import (
    IvRank,
    MarketSnapshot,
    OptionQuote,
    PutCallRatio,
    atm_iv30,
    hv30,
    iv_rank,
)
from app.domain.params import SHORT_PUT_GROUP, TRUE_WHEEL_GROUP, StrategyParams
from app.domain.pricing import bs_delta, bs_theta, bs_vega, prob_above, spread_pct
from app.domain.risk import (
    NO_TRADE_MESSAGES,
    AccountState,
    Exposure,
    Portfolio,
    Sizing,
    put_spread_stress_loss,
    short_put_stress_loss,
    size_deal,
    stress_move,
    utilization,
)
from app.domain.scoring import LegQuote, Quality, assess, finalize, portfolio_fit

PUT_CREDIT_SPREAD = "put_credit_spread"
# True Wheel put: cash secured, assignment accepted.
CASH_SECURED_PUT = "cash_secured_put"
# Short Put Income: a put sold for its premium and bought back before expiration.
SHORT_PUT = "short_put"
COVERED_CALL = "covered_call"
SINGLE_PUT_GROUPS = (SHORT_PUT_GROUP, TRUE_WHEEL_GROUP)

FUNNEL_STAGES = (
    "contracts",
    "iv_rank",
    "trend",
    "iv_hv",
    "put_call",
    "dte",
    "holding",
    "delta",
    "open_interest",
    "volume",
    "spread",
    "earnings",
    "structure",
    "return",
    "underlyings",
    "selected",
)


@dataclass(frozen=True)
class Leg:
    quote: OptionQuote
    side: str  # "sell" | "buy"
    delta: float


@dataclass
class Candidate:
    underlying: str
    group: str
    sector: str | None
    strategy: str
    expiration: date
    dte: int
    spot: float
    legs: list[Leg]
    # Per share, at mid. The natural credit (sell at bid, buy at ask) is the worst fill.
    credit: float
    natural_credit: float
    # Per unit (one spread, one contract), in dollars: contractual max loss, cash held by the
    # broker, and the loss if the underlying gaps down by the stress move.
    max_loss: float
    collateral: float
    stress_loss: float
    breakeven: float
    short_delta: float
    pop: float
    aroc: float
    iv_rank: IvRank | None
    iv30: float | None
    hv30: float | None
    next_earnings: date | None
    # Days between entry and the planned exit (entry DTE - exit DTE).
    holding_window: int = 0
    # Final score (the lowest quality score); before the portfolio is known, the lowest of the
    # absolute and execution scores.
    score: float = 0.0
    quantity: int = 0
    sizing: Sizing | None = None
    quality: Quality | None = None
    # Shares' cost basis, for a covered call.
    cost_basis: float | None = None
    # The underlying's put/call ratio (live chains only), for information and its filter.
    put_call: PutCallRatio | None = None

    @property
    def short_leg(self) -> Leg:
        return next(leg for leg in self.legs if leg.side == "sell")

    @property
    def distance_pct(self) -> float:
        """Distance from spot down to the sold strike, as a share of spot."""
        return (self.spot - self.short_leg.quote.strike) / self.spot if self.spot > 0 else 0.0

    @property
    def distance_sd(self) -> float | None:
        """The same distance in standard deviations of the move to expiration (IV and DTE)."""
        iv = self.short_leg.quote.iv
        strike = self.short_leg.quote.strike
        if iv <= 0 or self.dte <= 0 or strike <= 0 or self.spot <= 0:
            return None
        return math.log(self.spot / strike) / (iv * math.sqrt(self.dte / 365))

    @property
    def return_on_risk(self) -> float:
        """Not annualized: credit / max loss for a spread, credit / cash held for a put."""
        if self.strategy == COVERED_CALL:
            return self.credit / self.cost_basis if self.cost_basis else 0.0
        base = self.max_loss if self.strategy == PUT_CREDIT_SPREAD else self.collateral
        return self.credit * 100 / base if base > 0 else 0.0

    def greeks(self, today: date, rate: float) -> dict[str, float]:
        """Position greeks of one unit (100 shares per contract), seller's side: delta in
        shares, vega in dollars per IV point, theta in dollars per day."""
        out = {"delta": 0.0, "vega": 0.0, "theta": 0.0}
        years = max(0.0, (self.expiration - today).days / 365)
        for leg in self.legs:
            q = leg.quote
            sign = -100 if leg.side == "sell" else 100
            out["delta"] += sign * leg.delta
            out["vega"] += sign * bs_vega(self.spot, q.strike, years, q.iv, rate)
            out["theta"] += sign * bs_theta(q.option_type, self.spot, q.strike, years, q.iv, rate)
        return {k: round(v, 4) for k, v in out.items()}

    @property
    def abs_delta(self) -> float:
        return abs(self.short_delta)

    @property
    def iv_hv_ratio(self) -> float | None:
        return self.iv30 / self.hv30 if self.iv30 and self.hv30 else None


@dataclass(frozen=True)
class ShareLot:
    """Shares held after an assignment and not yet covered by a call."""

    underlying: str
    shares: int
    cost_basis: float  # per share, net of the put premium
    position_id: int | None = None


@dataclass(frozen=True)
class UnderlyingStats:
    spot: float
    iv30: float | None
    hv30: float | None
    iv_rank: IvRank | None
    # Close above its trend moving average; None when the history is too short.
    trend_up: bool | None = None
    put_call: PutCallRatio | None = None


@dataclass
class ScreenResult:
    funnel: dict[str, int]
    # Best candidate per underlying, ranked by score.
    ranked: list[Candidate]
    selected: list[Candidate]
    covered_calls: list[Candidate]
    underlyings: dict[str, UnderlyingStats] = field(default_factory=dict)
    # Why ranked candidates were not selected, by underlying.
    skipped: dict[str, str] = field(default_factory=dict)


def _years(today: date, expiration: date) -> float:
    return (expiration - today).days / 365


def _oi_ok(q: OptionQuote, p: StrategyParams) -> bool:
    return not p.use_open_interest_filter or q.open_interest >= p.min_open_interest


def _volume_ok(q: OptionQuote, p: StrategyParams) -> bool:
    return not p.use_volume_filter or q.volume >= p.min_volume


def _spread_ok(q: OptionQuote, p: StrategyParams) -> bool:
    return not p.use_spread_filter or spread_pct(q.bid, q.ask) <= p.max_spread_pct


def _liquid(q: OptionQuote, p: StrategyParams) -> bool:
    return _oi_ok(q, p) and _volume_ok(q, p) and _spread_ok(q, p)


def _earnings_ok(
    group: str, next_earnings: date | None, today: date, expiration: date, p: StrategyParams
) -> bool:
    """ETFs have no earnings. A stock with an unknown date is rejected rather than risked."""
    if group == "etf" or not p.use_earnings_filter:
        return True
    if next_earnings is None:
        return False
    return not today <= next_earnings <= expiration


def _assess(c: Candidate, stats: UnderlyingStats | None, p: StrategyParams) -> None:
    """Absolute and execution quality of a candidate; its score until the portfolio is known."""
    c.quality = assess(
        strategy=c.strategy,
        spot=c.spot,
        breakeven=c.breakeven,
        strike=c.short_leg.quote.strike,
        dte=c.dte,
        holding_window=c.holding_window,
        credit=c.credit,
        natural_credit=c.natural_credit,
        max_loss=c.max_loss,
        collateral=c.collateral,
        legs=[
            LegQuote(
                leg.quote.bid,
                leg.quote.ask,
                leg.quote.iv,
                leg.quote.open_interest,
                leg.quote.volume,
            )
            for leg in c.legs
        ],  # fmt: skip
        iv_rank=c.iv_rank.value if c.iv_rank else None,
        iv_rank_method=c.iv_rank.method if c.iv_rank else None,
        iv_hv=c.iv_hv_ratio,
        hv=c.hv30,
        trend_up=stats.trend_up if stats else None,
        params=p,
        cost_basis=c.cost_basis,
    )
    c.score = c.quality.pre_score


def trend_ok(closes: Sequence[float], p: StrategyParams) -> bool:
    """Last close above its simple moving average; too short a history fails the filter."""
    if not p.use_trend_filter:
        return True
    n = p.trend_sma_days
    if len(closes) < n:
        return False
    return closes[-1] > sum(closes[-n:]) / n


def _iv_hv_ok(stats: "UnderlyingStats", p: StrategyParams) -> bool:
    if not p.use_iv_hv_filter:
        return True
    return bool(stats.iv30 and stats.hv30) and stats.iv30 / stats.hv30 >= p.min_iv_hv_ratio


def put_call_ok(stats: "UnderlyingStats", p: StrategyParams) -> bool:
    """Volume put/call ratio at most the maximum. Too little volume, or no data (the backtest's
    synthetic chains), lets the trade through: the filter cannot judge it."""
    if not p.use_put_call_filter:
        return True
    pc = stats.put_call
    if pc is None or pc.volume_ratio is None or pc.total_volume < p.min_put_call_volume:
        return True
    return pc.volume_ratio <= p.max_put_call_ratio


def _put_trades(
    snap: MarketSnapshot,
    group: str,
    stats: UnderlyingStats,
    today: date,
    p: StrategyParams,
    funnel: Counter,
    capital: float,
) -> list[Candidate]:
    """Every put trade passing the filters. Spreads take the first width whose max loss fits
    the risk budget of one trade (else the first usable width, which then gets no contract)."""
    puts = [q for q in snap.options if q.option_type == "put"]
    single = group in SINGLE_PUT_GROUPS
    accepted = group == TRUE_WHEEL_GROUP
    if single and p.use_wheel_max_strike:
        puts = [q for q in puts if q.strike <= p.wheel_max_strike]
    funnel["contracts"] += len(puts)
    if p.use_iv_rank_filter and (stats.iv_rank is None or stats.iv_rank.value < p.min_iv_rank):
        return []
    funnel["iv_rank"] += len(puts)
    if not trend_ok(snap.closes, p):
        return []
    funnel["trend"] += len(puts)
    if not _iv_hv_ok(stats, p):
        return []
    funnel["iv_hv"] += len(puts)
    if not put_call_ok(stats, p):
        return []
    funnel["put_call"] += len(puts)

    rows = [q for q in puts if p.dte_min <= (q.expiration - today).days <= p.dte_max]
    funnel["dte"] += len(rows)
    rows = [
        q
        for q in rows
        if p.holding_window((q.expiration - today).days, accepted) >= p.min_holding_days
    ]
    funnel["holding"] += len(rows)
    deltas = {
        q.symbol: bs_delta(
            "put", snap.spot, q.strike, _years(today, q.expiration), q.iv, p.risk_free_rate
        )
        for q in rows
    }
    rows = [q for q in rows if p.delta_min <= abs(deltas[q.symbol]) <= p.delta_max]
    funnel["delta"] += len(rows)
    rows = [q for q in rows if _oi_ok(q, p)]
    funnel["open_interest"] += len(rows)
    rows = [q for q in rows if _volume_ok(q, p)]
    funnel["volume"] += len(rows)
    rows = [q for q in rows if _spread_ok(q, p)]
    funnel["spread"] += len(rows)
    rows = [q for q in rows if _earnings_ok(group, snap.next_earnings, today, q.expiration, p)]
    funnel["earnings"] += len(rows)

    by_key = {(q.expiration, q.strike): q for q in puts}
    sector = "ETF" if group == "etf" else snap.sector
    move = stress_move(snap.symbol, sector, p)
    unit_budget = capital * p.max_trade_risk_pct
    trades: list[Candidate] = []
    for short in rows:
        dte = (short.expiration - today).days
        years = _years(today, short.expiration)
        short_leg = Leg(short, "sell", deltas[short.symbol])
        if single:
            credit = short.mid
            if credit <= 0:
                continue
            legs = [short_leg]
            natural = short.bid
            max_loss = (short.strike - credit) * 100
            collateral = short.strike * 100
            stress = short_put_stress_loss(snap.spot, short.strike, credit, move)
            strategy = CASH_SECURED_PUT if accepted else SHORT_PUT
        else:
            spread = _build_spread(short, by_key, p, unit_budget)
            if spread is None:
                continue
            long, width = spread
            credit = short.mid - long.mid
            natural = short.bid - long.ask
            long_delta = bs_delta("put", snap.spot, long.strike, years, long.iv, p.risk_free_rate)
            legs = [short_leg, Leg(long, "buy", long_delta)]
            max_loss = (width - credit) * 100
            collateral = max_loss
            stress = put_spread_stress_loss(snap.spot, short.strike, long.strike, credit, move)
            strategy = PUT_CREDIT_SPREAD
        breakeven = short.strike - credit
        trades.append(
            Candidate(
                underlying=snap.symbol,
                group=group,
                sector=sector,
                strategy=strategy,
                expiration=short.expiration,
                dte=dte,
                spot=snap.spot,
                legs=legs,
                credit=credit,
                natural_credit=natural,
                max_loss=max_loss,
                collateral=collateral,
                stress_loss=stress,
                breakeven=breakeven,
                short_delta=short_leg.delta,
                pop=prob_above(snap.spot, breakeven, years, short.iv, p.risk_free_rate),
                aroc=credit * 100 / max_loss * 365 / dte,
                iv_rank=stats.iv_rank,
                iv30=stats.iv30,
                hv30=stats.hv30,
                next_earnings=snap.next_earnings,
                holding_window=p.holding_window(dte, accepted),
                put_call=stats.put_call,
            )
        )
    funnel["structure"] += len(trades)
    trades = [t for t in trades if _return_ok(t, p)]
    funnel["return"] += len(trades)
    return trades


def _return_ok(t: Candidate, p: StrategyParams) -> bool:
    if p.use_aroc_filter and t.aroc < p.min_aroc:
        return False
    if p.use_ror_filter:
        floor = p.min_ror_spread if t.strategy == PUT_CREDIT_SPREAD else p.min_ror_put
        return t.return_on_risk >= floor
    return True


def _build_spread(
    short: OptionQuote,
    by_key: Mapping[tuple[date, float], OptionQuote],
    p: StrategyParams,
    unit_budget: float = float("inf"),
) -> tuple[OptionQuote, float] | None:
    """Long put `width` below the short: the first width in preference order with a usable
    leg and a max loss within `unit_budget`, else the first usable width."""
    fallback = None
    for width in p.spread_widths:
        long = by_key.get((short.expiration, short.strike - width))
        if long is None or long.ask <= 0:
            continue
        if not _oi_ok(long, p) or not _spread_ok(long, p):
            continue
        credit = short.mid - long.mid
        if credit < p.min_credit or short.bid - long.ask <= 0 or credit >= width:
            continue
        if (width - credit) * 100 <= unit_budget:
            return long, width
        fallback = fallback or (long, width)
    return fallback


def _covered_call(
    snap: MarketSnapshot, lot: ShareLot, stats: UnderlyingStats, today: date, p: StrategyParams
) -> Candidate | None:
    """Best call to sell against held shares: strike at or above cost basis, usual filters.

    No IV Rank, earnings or AROC filter: the shares are already held, and the call only adds
    premium. PoP is the probability that shares plus premium end above the cost basis.
    """
    calls: list[Candidate] = []
    for q in snap.options:
        dte = (q.expiration - today).days
        if q.option_type != "call" or not p.dte_min <= dte <= p.dte_max:
            continue
        if q.strike < lot.cost_basis or not _liquid(q, p) or q.mid <= 0:
            continue
        years = _years(today, q.expiration)
        delta = bs_delta("call", snap.spot, q.strike, years, q.iv, p.risk_free_rate)
        if not p.delta_min <= delta <= p.delta_max:
            continue
        breakeven = lot.cost_basis - q.mid
        calls.append(
            Candidate(
                underlying=snap.symbol,
                group=TRUE_WHEEL_GROUP,
                sector=snap.sector,
                strategy=COVERED_CALL,
                expiration=q.expiration,
                dte=dte,
                spot=snap.spot,
                legs=[Leg(q, "sell", delta)],
                credit=q.mid,
                natural_credit=q.bid,
                max_loss=0.0,
                collateral=0.0,
                stress_loss=0.0,
                breakeven=breakeven,
                short_delta=delta,
                pop=prob_above(snap.spot, breakeven, years, q.iv, p.risk_free_rate),
                aroc=q.mid / lot.cost_basis * 365 / dte,
                iv_rank=stats.iv_rank,
                iv30=stats.iv30,
                hv30=stats.hv30,
                next_earnings=snap.next_earnings,
                holding_window=dte,
                quantity=lot.shares // 100,
                cost_basis=lot.cost_basis,
                put_call=stats.put_call,
            )
        )
    for c in calls:
        _assess(c, stats, p)
    return max(calls, key=lambda c: c.score, default=None)


def exposure_of(c: Candidate) -> Exposure:
    """A sized candidate as a portfolio exposure (totals over its quantity)."""
    return Exposure(
        underlying=c.underlying,
        sector=c.sector,
        strategy=c.strategy,
        max_loss=c.max_loss * c.quantity,
        stress_loss=c.stress_loss * c.quantity,
        collateral=c.collateral * c.quantity,
        expiration=c.expiration,
    )


def underlying_stats(
    snap: MarketSnapshot, iv_history: Sequence[float], today: date, p: StrategyParams
) -> UnderlyingStats:
    iv30 = atm_iv30(snap, today)
    rank = iv_rank(iv30, iv_history, snap.closes, p.iv_rank_min_history) if iv30 else None
    n = p.trend_sma_days
    trend = snap.closes[-1] > sum(snap.closes[-n:]) / n if len(snap.closes) >= n else None
    return UnderlyingStats(snap.spot, iv30, hv30(snap.closes), rank, trend, snap.put_call)


def screen(
    snapshots: Sequence[MarketSnapshot],
    today: date,
    params: StrategyParams,
    account: AccountState,
    iv_history: Mapping[str, Sequence[float]] | None = None,
    share_lots: Sequence[ShareLot] = (),
    open_underlyings: set[str] | frozenset[str] = frozenset(),
    exposures: Sequence[Exposure] = (),
    cooling_down: set[str] | frozenset[str] = frozenset(),
) -> ScreenResult:
    """Run the full funnel and pick at most `max_deals` new trades that fit the risk limits.

    `iv_history` holds past daily IV30 values per underlying (today excluded).
    `open_underlyings` already have a position: no second entry on them.
    `exposures` are the open and pending positions: every limit (open max loss, clusters,
    sectors, collateral) counts them along with the day's new deals.
    `cooling_down` had a position closed within `reentry_cooldown_days`.
    """
    iv_history = iv_history or {}
    funnel: Counter = Counter({stage: 0 for stage in FUNNEL_STAGES})
    stats: dict[str, UnderlyingStats] = {}
    trades: list[Candidate] = []
    for snap in snapshots:
        stats[snap.symbol] = underlying_stats(snap, iv_history.get(snap.symbol, ()), today, params)
        group = params.group_of(snap.symbol)
        if group is None:
            continue
        trades.extend(
            _put_trades(snap, group, stats[snap.symbol], today, params, funnel, account.capital)
        )

    for t in trades:
        _assess(t, stats.get(t.underlying), params)
    best: dict[str, Candidate] = {}
    for t in trades:
        if t.underlying not in best or t.score > best[t.underlying].score:
            best[t.underlying] = t
    ranked = sorted(best.values(), key=lambda c: c.score, reverse=True)
    funnel["underlyings"] = len(ranked)

    selected: list[Candidate] = []
    skipped: dict[str, str] = {}
    # Collateral the caller counts in `account` but that no exposure carries.
    other = max(0.0, account.engaged - sum(e.collateral for e in exposures))
    portfolio = Portfolio(account.capital, tuple(exposures), other)
    held = set(open_underlyings) | portfolio.underlyings
    for c in ranked:
        if len(selected) >= params.max_deals:
            skipped[c.underlying] = "max_deals"
            continue
        if c.underlying in held:
            skipped[c.underlying] = "already_open"
            continue
        if c.underlying in cooling_down:
            skipped[c.underlying] = "cooldown"
            continue
        if (
            params.use_sector_limit
            and c.sector
            and portfolio.sector_count(c.sector) >= params.max_per_sector
        ):
            skipped[c.underlying] = "sector_limit"
            continue
        if c.quality is not None and c.quality.pre_score < params.min_quality_score:
            finalize(c.quality, None, params)  # blocking reasons of the trade itself
            skipped[c.underlying] = "low_quality"
            continue
        c.sizing = size_deal(
            underlying=c.underlying,
            sector=c.sector,
            strategy=c.strategy,
            max_loss=c.max_loss,
            stress_loss=c.stress_loss,
            collateral=c.collateral,
            portfolio=portfolio,
            params=params,
            score=c.score,
            expiration=c.expiration,
        )
        if c.sizing.quantity == 0:
            skipped[c.underlying] = c.sizing.no_trade or "risk_budget"
            continue
        c.quantity = c.sizing.quantity
        exposure = exposure_of(c)
        after = portfolio.with_exposure(exposure)
        finalize(c.quality, portfolio_fit(max(utilization(after, exposure, params).values())),
                 params)  # fmt: skip
        c.score = c.quality.final or 0.0
        if not c.quality.eligible:
            skipped[c.underlying] = "low_quality"
            c.quantity = 0
            continue
        portfolio = after
        held.add(c.underlying)
        selected.append(c)
    funnel["selected"] = len(selected)
    for i, c in enumerate(ranked, start=1):
        if c.quality is None:
            continue
        c.quality.rank, c.quality.rank_of = i, len(ranked)
        reason = skipped.get(c.underlying)
        if reason and reason != "low_quality":
            c.quality.blocking.append(NO_TRADE_MESSAGES[reason])

    by_symbol = {s.symbol: s for s in snapshots}
    covered_calls = []
    for lot in share_lots:
        snap = by_symbol.get(lot.underlying)
        if snap is None or lot.shares < 100:
            continue
        cc = _covered_call(snap, lot, stats[lot.underlying], today, params)
        if cc is not None:
            covered_calls.append(cc)

    return ScreenResult(dict(funnel), ranked, selected, covered_calls, stats, skipped)
