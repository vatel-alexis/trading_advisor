"""Read models for the interface: dashboard, today's deals, open positions and the trade log.

Amounts go out as floats (display only); every decision is taken on the stored decimals.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.exits import ShortPremium, expected_exit, stop_price, stop_rules
from app.domain.params import StrategyParams
from app.domain.risk import Exposure, Portfolio, cluster_of, expiration_concentration
from app.models import (
    Decision,
    Opportunity,
    Order,
    Position,
    PositionEvent,
    PositionLeg,
    PositionMark,
    ScreenerRun,
    ShadowOutcome,
)
from app.models.enums import (
    InstrumentType,
    OpportunityStatus,
    OrderPurpose,
    PositionEventType,
    PositionStatus,
    Side,
    StrategyType,
)
from app.services.safety import losses, open_exposures
from app.services.screening import ACTIVE, account_state, active_config
from app.services.trading import LIVE, position_params

FINISHED = (
    PositionStatus.CLOSED,
    PositionStatus.EXPIRED,
    PositionStatus.ASSIGNED,
    PositionStatus.CANCELED,
)
# Trade log kinds: finished position statuses plus unanswered or rejected proposals.
HISTORY_KINDS = (*(s.value for s in FINISHED), "rejected", "ignored")


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _params(session: Session) -> StrategyParams:
    return StrategyParams.from_dict(active_config(session).params)


def _latest_marks(session: Session, position_ids: list[int]) -> dict[int, PositionMark]:
    if not position_ids:
        return {}
    query = (
        select(PositionMark)
        .where(PositionMark.position_id.in_(position_ids))
        .distinct(PositionMark.position_id)
        .order_by(PositionMark.position_id, PositionMark.marked_at.desc())
    )
    return {mark.position_id: mark for mark in session.scalars(query).all()}


def _stock_leg(position: Position) -> PositionLeg | None:
    return next((leg for leg in position.legs if leg.instrument_type == InstrumentType.STOCK), None)


# --- dashboard ------------------------------------------------------------------------------


def portfolio_greeks(session: Session) -> dict[str, Any]:
    """Delta (shares), vega ($ per IV point) and theta ($ per day) of open and pending
    positions, from the greeks computed when each deal was screened. Positions opened before
    they were recorded are counted in `missing` ("donnée absente"), never guessed."""
    total = {"delta": 0.0, "vega": 0.0, "theta": 0.0}
    missing = 0
    positions = session.scalars(
        select(Position).where(Position.status.in_(ACTIVE)).options(selectinload(Position.legs))
    ).all()
    for position in positions:
        stock = _stock_leg(position)
        if stock is not None:
            total["delta"] += stock.quantity
            continue
        opportunity = (
            session.get(Opportunity, position.opportunity_id) if position.opportunity_id else None
        )
        greeks = ((opportunity.metrics or {}) if opportunity else {}).get("greeks")
        if not greeks or not position.legs:
            missing += 1
            continue
        quantity = position.legs[0].quantity
        for key in total:
            total[key] += float(greeks.get(key, 0.0)) * quantity
    return {
        **{k: round(v, 2) for k, v in total.items()},
        "missing": missing,
        "basis": "à l'entrée",
    }


def dashboard(
    session: Session, starting_capital: float, today: date | None = None
) -> dict[str, Any]:
    params = _params(session)
    account = account_state(session, starting_capital)
    open_ids = list(
        session.scalars(
            select(Position.id).where(
                Position.status == PositionStatus.OPEN, Position.strategy_type.is_not(None)
            )
        ).all()
    )
    marks = _latest_marks(session, open_ids)
    counts = dict(
        session.execute(
            select(Position.status, func.count())
            .where(Position.status.in_(ACTIVE))
            .group_by(Position.status)
        ).all()
    )
    proposed = session.scalar(
        select(func.count())
        .select_from(Opportunity)
        .where(Opportunity.status == OpportunityStatus.PROPOSED)
    )
    last_run = session.scalar(select(func.max(ScreenerRun.finished_at)))
    today = today or datetime.now(MARKET_TZ).date()
    unrealized = round(sum(float(m.unrealized_pnl) for m in marks.values()), 2)
    book = Portfolio(account.capital, tuple(open_exposures(session, params)))
    state = losses(session, starting_capital, today, account.capital + unrealized)
    return {
        # Kept apart: cash held by the broker, what the contracts can lose at most, and what
        # a gap down by the stress move would cost.
        "cash_available": round(account.capital - book.engaged, 2),
        "collateral": round(book.engaged, 2),
        "open_max_loss": round(book.open_max_loss, 2),
        "open_max_loss_limit": round(account.capital * params.max_open_risk_pct, 2),
        "stress_loss": round(book.stress_loss, 2),
        "clusters": [
            {"cluster": key, "risk": round(value, 2)}
            for key, value in sorted(book.clusters(params).items(), key=lambda kv: -kv[1])
        ],
        "cluster_limit": round(account.capital * params.max_cluster_risk_pct, 2),
        "expirations": [
            {"expiration": key, "risk": round(value, 2)}
            for key, value in expiration_concentration(book.exposures).items()
        ],
        "expiration_limit": round(account.capital * params.max_expiration_risk_pct, 2),
        "greeks": portfolio_greeks(session),
        "equity": round(state.equity, 2),
        "daily_change": round(state.daily, 4),
        "monthly_change": round(state.monthly, 4),
        "drawdown": round(state.drawdown, 4),
        "limits": {
            "daily_loss": params.max_daily_loss_pct,
            "monthly_loss": params.max_monthly_loss_pct,
            "drawdown": params.max_drawdown_pct,
            "trade_risk": params.max_trade_risk_pct,
        },
        "starting_capital": float(starting_capital),
        "realized_pnl": round(account.capital - float(starting_capital), 2),
        "capital": round(account.capital, 2),
        "engaged": round(account.engaged, 2),
        "available": round(account.capital - account.engaged, 2),
        "max_engaged_pct": params.max_engaged_pct,
        "max_trade_pct": params.max_trade_pct,
        "engagement_capacity": round(account.remaining_capacity(params), 2),
        "unrealized_pnl": unrealized,
        "open_positions": counts.get(PositionStatus.OPEN, 0),
        "pending_positions": counts.get(PositionStatus.PENDING, 0),
        "proposed_opportunities": proposed or 0,
        "last_screener_run": _iso(last_run),
        "analytics": analytics(session, starting_capital, today),
    }


# --- analytics ------------------------------------------------------------------------------

MARKET_TZ = ZoneInfo("America/New_York")


def _market_day(moment: datetime) -> date:
    return moment.astimezone(MARKET_TZ).date()


def _counts_as_trade(p: Position) -> bool:
    """A finished trade for the win rate.

    An assigned put is not one: its premium went into the shares' cost basis, so its outcome
    is the share lot's, counted when the shares are called away.
    """
    if p.status == PositionStatus.CANCELED or p.realized_pnl is None:
        return False
    return not (
        p.status == PositionStatus.ASSIGNED
        and p.strategy_type in (StrategyType.CASH_SECURED_PUT, StrategyType.SHORT_PUT)
    )


def _win_stats(pnls: list[float]) -> dict[str, Any]:
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    lost = -sum(losses)
    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "realized_pnl": round(sum(pnls), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        # Gross gains over gross losses; None while nothing was lost.
        "profit_factor": round(sum(wins) / lost, 2) if lost > 0 else None,
    }


def analytics(session: Session, starting_capital: float, today: date) -> dict[str, Any]:
    """Realized-basis performance: P&L curve, win rate, premiums collected.

    Only finished positions count, except a partial close on a still open position, whose
    realized part is added to the curve on today's date so it ends on the account's capital.
    """
    booked = session.scalars(select(Position).where(Position.realized_pnl.is_not(None))).all()
    finished = [p for p in booked if p.status in FINISHED and p.closed_at is not None]

    by_day: dict[date, float] = {}
    for p in finished:
        day = _market_day(p.closed_at)
        by_day[day] = by_day.get(day, 0.0) + float(p.realized_pnl)
    partial = sum(float(p.realized_pnl) for p in booked if p not in finished)
    if partial:
        by_day[today] = by_day.get(today, 0.0) + partial

    curve = []
    cumulative = 0.0
    peak = float(starting_capital)
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    for day in sorted(by_day):
        cumulative += by_day[day]
        equity = float(starting_capital) + cumulative
        peak = max(peak, equity)
        if peak - equity > max_drawdown:
            max_drawdown = peak - equity
            max_drawdown_pct = max_drawdown / peak
        curve.append(
            {
                "date": day.isoformat(),
                "pnl": round(by_day[day], 2),
                "cumulative": round(cumulative, 2),
                "equity": round(equity, 2),
            }
        )

    trades = [p for p in finished if _counts_as_trade(p)]
    strategies: dict[str, list[float]] = {}
    exits: dict[str, int] = {}
    months: dict[str, dict[str, float]] = {}

    def month(key: str) -> dict[str, float]:
        return months.setdefault(key, {"premium": 0.0, "realized_pnl": 0.0, "trades": 0})

    for p in trades:
        pnl = float(p.realized_pnl)
        key = p.strategy_type.value if p.strategy_type else "shares"
        strategies.setdefault(key, []).append(pnl)
        reason = p.exit_reason.value if p.exit_reason else "other"
        exits[reason] = exits.get(reason, 0) + 1
        row = month(_market_day(p.closed_at).strftime("%Y-%m"))
        row["realized_pnl"] += pnl
        row["trades"] += 1

    # Gross premium: every option sold, at its fill credit and filled size, in the month it
    # opened, whatever happened next (an assigned put's premium lowers the shares' cost).
    opened = session.execute(
        select(PositionEvent.payload, PositionEvent.occurred_at)
        .join(Position)
        .where(
            PositionEvent.type == PositionEventType.OPENED,
            Position.strategy_type.is_not(None),
        )
    ).all()
    for payload, occurred_at in opened:
        payload = payload or {}
        premium = float(payload.get("credit", 0)) * 100 * int(payload.get("contracts", 0))
        month(_market_day(occurred_at).strftime("%Y-%m"))["premium"] += premium

    this_month = today.strftime("%Y-%m")
    return {
        **_win_stats([float(p.realized_pnl) for p in trades]),
        "premium_collected": round(sum(m["premium"] for m in months.values()), 2),
        "premium_this_month": round(months.get(this_month, {}).get("premium", 0.0), 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "curve": curve,
        "months": [
            {
                "month": key,
                "premium": round(m["premium"], 2),
                "realized_pnl": round(m["realized_pnl"], 2),
                "trades": int(m["trades"]),
            }
            for key, m in sorted(months.items())
        ],
        "by_strategy": [
            {"strategy": key, **_win_stats(pnls)} for key, pnls in sorted(strategies.items())
        ],
        "exit_reasons": dict(sorted(exits.items(), key=lambda item: -item[1])),
    }


# --- opportunities --------------------------------------------------------------------------


def _opportunity(o: Opportunity, capital: float) -> dict[str, Any]:
    metrics = o.metrics or {}
    quantity = o.legs[0].quantity if o.legs else int(metrics.get("quantity", 1))
    credit_total = float(o.credit) * 100 * quantity
    max_loss = float(o.max_loss)
    target = metrics.get("take_profit_price")
    short = next((leg for leg in o.legs if leg.side == Side.SELL), None)
    return {
        "id": o.id,
        "underlying": o.underlying,
        "sector": o.sector,
        "strategy": o.strategy_type.value,
        "status": o.status.value,
        "proposed_at": _iso(o.created_at),
        "expiration": _iso(o.expiration),
        "dte": o.dte,
        "underlying_price": float(o.underlying_price),
        "short_strike": _f(short.strike) if short else None,
        "legs": [
            {
                "symbol": leg.option_symbol,
                "type": leg.option_type.value,
                "side": leg.side.value,
                "strike": float(leg.strike),
                "bid": float(leg.bid),
                "ask": float(leg.ask),
                "delta": _f(leg.delta),
            }
            for leg in sorted(o.legs, key=lambda leg: leg.side != Side.SELL)
        ],
        "quantity": quantity,
        "credit": float(o.credit),
        "credit_total": round(credit_total, 2),
        "max_loss": max_loss,
        "collateral": float(o.collateral),
        "stress_loss": _f(o.stress_loss),
        "risk_pct": round(
            (
                float(o.stress_loss)
                if o.strategy_type != StrategyType.PUT_CREDIT_SPREAD and o.stress_loss is not None
                else max_loss
            )
            / capital,
            4,
        )
        if capital > 0
        else None,
        "sizing": metrics.get("sizing"),
        "cluster": metrics.get("cluster"),
        "weight": round(float(o.collateral) / capital, 4) if capital > 0 else None,
        "breakeven": float(o.breakeven),
        "delta": float(o.short_delta),
        "pop": float(o.pop),
        # Return on risk: the credit over what can be lost.
        "ror": round(credit_total / max_loss, 4) if max_loss > 0 else None,
        "aroc": float(o.aroc),
        "iv_rank": _f(o.iv_rank),
        "iv_rank_method": metrics.get("iv_rank_method"),
        # Put/call ratio of the underlying's chain (older deals and backtests: None).
        "put_call": metrics.get("put_call"),
        "score": float(o.score),
        "next_earnings": _iso(o.next_earnings),
        "take_profit_price": target,
        "take_profit_gain": (
            None if target is None else round((float(o.credit) - target) * 100 * quantity, 2)
        ),
        "stop_price": metrics.get("stop_price"),
        "time_exit_date": metrics.get("time_exit_date"),
        "assignment_accepted": bool(metrics.get("assignment_accepted")),
        # Entry DTE - exit DTE, and how far the sold strike is from spot (older deals: None).
        "holding_window": metrics.get("holding_window"),
        "distance_pct": metrics.get("distance_pct"),
        "distance_sd": metrics.get("distance_sd"),
        # Quality scores and blocking rules (older deals: None), return not annualized.
        "quality": metrics.get("quality"),
        "return_on_risk": metrics.get("return_on_risk"),
        "greeks": metrics.get("greeks"),
        "max_gain": round(credit_total, 2),
    }


def _impact(o: Opportunity, book: Portfolio, params: StrategyParams) -> dict[str, Any]:
    """The book's risk once this deal is added, next to each limit."""
    strategy = o.strategy_type.value
    exposure = Exposure(
        underlying=o.underlying,
        sector=o.sector,
        strategy=strategy,
        max_loss=float(o.max_loss),
        stress_loss=float(o.stress_loss if o.stress_loss is not None else o.max_loss),
        collateral=float(o.collateral),
        expiration=o.expiration,
    )
    after = book.with_exposure(exposure)
    cluster = cluster_of(o.underlying, o.sector, params)
    capital = book.capital
    return {
        "open_max_loss_after": round(after.open_max_loss, 2),
        "open_max_loss_limit": round(capital * params.max_open_risk_pct, 2),
        "cluster": cluster,
        "cluster_risk_after": round(after.cluster_risk(cluster, params), 2),
        "cluster_limit": round(capital * params.max_cluster_risk_pct, 2),
        "expiration_risk_after": round(after.expiration_risk(o.expiration), 2),
        "expiration_limit": round(capital * params.max_expiration_risk_pct, 2),
        "stress_loss_after": round(after.stress_loss, 2),
        "collateral_after": round(after.engaged, 2),
    }


def opportunities(
    session: Session, starting_capital: float, status: OpportunityStatus | None
) -> list[dict[str, Any]]:
    capital = account_state(session, starting_capital).capital
    query = select(Opportunity).options(selectinload(Opportunity.legs))
    if status is not None:
        query = query.where(Opportunity.status == status)
    query = query.order_by(Opportunity.created_at.desc(), Opportunity.score.desc()).limit(50)
    params = _params(session)
    book = Portfolio(capital, tuple(open_exposures(session, params)))
    out = []
    for o in session.scalars(query).all():
        card = _opportunity(o, capital)
        if o.status == OpportunityStatus.PROPOSED:
            card["impact"] = _impact(o, book, params)
        out.append(card)
    return out


def no_trade(session: Session) -> dict[str, Any]:
    """The last screener run's candidates that got no contract, with every exact reason."""
    run = session.scalar(
        select(ScreenerRun)
        .where(ScreenerRun.finished_at.is_not(None))
        .order_by(ScreenerRun.finished_at.desc())
        .limit(1)
    )
    if run is None:
        return {"run_at": None, "rows": []}
    counts = run.filter_counts or {}
    rows = counts.get("rejected")
    if rows is None:  # runs before the quality scores: the single reason only
        rows = [
            {"underlying": k, "strategy": None, "reasons": [v], "quality": None}
            for k, v in (counts.get("no_trade") or {}).items()
        ]
    return {"run_at": _iso(run.finished_at), "rows": rows}


# --- open positions -------------------------------------------------------------------------


def _orders_by_position(session: Session, ids: list[int]) -> dict[int, list[Order]]:
    rows: dict[int, list[Order]] = {}
    if ids:
        query = select(Order).where(Order.position_id.in_(ids), Order.status.in_(LIVE))
        for order in session.scalars(query).all():
            rows.setdefault(order.position_id, []).append(order)
    return rows


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 4)


def _execution(
    p: Position, mark: PositionMark | None, params: StrategyParams, rules: list[dict[str, str]]
) -> dict[str, Any]:
    """Quotes against fills, per share: entry slippage (mid - fill credit, positive when the
    fill gave up part of the mid), the liquidation spread and the expected buy-back now."""
    credit = _f(p.entry_credit)
    entry_mid, entry_natural = _f(p.entry_mid), _f(p.entry_natural)
    mid = _f(mark.mark) if mark else None
    natural = _f(mark.natural) if mark else None
    expected = expected_exit(mid, natural, params) if mid is not None else None
    estimated = None
    if entry_mid is not None and entry_natural is not None:
        estimated = round(params.exit_slippage_share * max(entry_mid - entry_natural, 0), 4)
    return {
        "entry_mid": entry_mid,
        "entry_natural": entry_natural,
        "entry_fill": credit,
        "entry_slippage": _diff(entry_mid, credit),
        "entry_slippage_estimated": estimated,
        "mid": mid,
        "natural": natural,
        "liquidation_spread": _diff(natural, mid),
        "expected_exit": None if expected is None else round(expected, 4),
        "delta": _f(mark.delta) if mark else None,
        "underlying_price": _f(mark.underlying_price) if mark else None,
        "stop_price": (
            stop_price(credit, params) if credit and rules and params.use_stop_loss else None
        ),
        "stop_rules": rules,
        "limit_steps": params.limit_steps,
    }


def positions(session: Session, today: date) -> dict[str, Any]:
    """Pending and open option positions with their last mark, and the share lots held."""
    rows = session.scalars(
        select(Position)
        .options(selectinload(Position.legs))
        .where(Position.status.in_(ACTIVE))
        .order_by(Position.created_at)
    ).all()
    options = [p for p in rows if p.strategy_type is not None]
    lots = [p for p in rows if p.strategy_type is None]
    marks = _latest_marks(session, [p.id for p in options])
    live = _orders_by_position(session, [p.id for p in rows])

    covered: dict[int, int] = {}
    for p in options:
        if p.parent_position_id is not None:
            covered[p.parent_position_id] = covered.get(p.parent_position_id, 0) + sum(
                100 * leg.quantity for leg in p.legs if leg.side == Side.SELL
            )

    option_rows = []
    for p in options:
        legs = [leg for leg in p.legs if leg.instrument_type == InstrumentType.OPTION]
        expiration = legs[0].expiration if legs else None
        orders = live.get(p.id, [])
        target = next((o for o in orders if o.purpose == OrderPurpose.TAKE_PROFIT), None)
        opening = next((o for o in orders if o.purpose == OrderPurpose.OPEN), None)
        exit_order = next(
            (o for o in orders if o.purpose not in (OrderPurpose.OPEN, OrderPurpose.TAKE_PROFIT)),
            None,
        )
        mark = marks.get(p.id)
        credit = _f(p.entry_credit)
        params = position_params(session, p)
        short = next((leg for leg in legs if leg.side == Side.SELL), None)
        accepted = p.strategy_type == StrategyType.CASH_SECURED_PUT and params.assignment_accepted(
            p.underlying
        )
        rules = stop_rules(
            ShortPremium(p.strategy_type.value, credit or 0.0, expiration or today, accepted),
            params,
            _f(short.strike) if short else None,
        )
        profit_pct = None
        if mark is not None and credit:
            profit_pct = round((credit - float(mark.mark)) / credit, 4)
        option_rows.append(
            {
                "id": p.id,
                "underlying": p.underlying,
                "strategy": p.strategy_type.value,
                "status": p.status.value,
                "opened_at": _iso(p.opened_at),
                "expiration": _iso(expiration),
                "dte": (expiration - today).days if expiration else None,
                "contracts": legs[0].quantity if legs else 0,
                "legs": [
                    {
                        "symbol": leg.symbol,
                        "type": leg.option_type.value if leg.option_type else None,
                        "side": leg.side.value,
                        "strike": _f(leg.strike),
                    }
                    for leg in sorted(legs, key=lambda leg: leg.side != Side.SELL)
                ],
                "entry_credit": credit,
                "open_limit": _f(opening.limit_price) if opening else None,
                "open_step": opening.reprice_step if opening else None,
                "collateral": float(p.collateral),
                "max_loss": _f(p.max_loss),
                "mark": _f(mark.mark) if mark else None,
                "marked_at": _iso(mark.marked_at) if mark else None,
                "unrealized_pnl": _f(mark.unrealized_pnl) if mark else None,
                "profit_pct": profit_pct,
                "take_profit_price": _f(target.limit_price) if target else None,
                "exit_order": (
                    {
                        "purpose": exit_order.purpose.value,
                        "limit": _f(exit_order.limit_price),
                        "step": exit_order.reprice_step,
                    }
                    if exit_order
                    else None
                ),
                "execution": _execution(p, mark, params, rules),
                "can_close": p.status == PositionStatus.OPEN and exit_order is None,
            }
        )

    lot_rows = []
    for p in lots:
        stock = _stock_leg(p)
        shares = stock.quantity if stock else 0
        lot_rows.append(
            {
                "id": p.id,
                "underlying": p.underlying,
                "opened_at": _iso(p.opened_at),
                "shares": shares,
                "cost_basis": _f(stock.avg_price) if stock else None,
                "collateral": float(p.collateral),
                "covered_shares": min(covered.get(p.id, 0), shares),
            }
        )
    return {"options": option_rows, "share_lots": lot_rows}


# --- trade log ------------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryFilter:
    kinds: tuple[str, ...] = ()
    underlying: str | None = None
    strategy: str | None = None
    since: date | None = None
    until: date | None = None


def _in_range(day: date | None, f: HistoryFilter) -> bool:
    if day is None:
        return f.since is None and f.until is None
    return (f.since is None or day >= f.since) and (f.until is None or day <= f.until)


def history(session: Session, f: HistoryFilter, limit: int = 500) -> list[dict[str, Any]]:
    """Finished positions and the proposals that were rejected or left unanswered, newest first.

    Rejected and ignored deals stay in the log so selection bias can be measured later.
    """
    kinds = set(f.kinds or HISTORY_KINDS)
    rows: list[dict[str, Any]] = []

    statuses = [PositionStatus(k) for k in kinds if k in {s.value for s in FINISHED}]
    if statuses:
        query = (
            select(Position)
            .options(selectinload(Position.legs))
            .where(Position.status.in_(statuses))
        )
        if f.underlying:
            query = query.where(Position.underlying == f.underlying.upper())
        if f.strategy == "shares":
            query = query.where(Position.strategy_type.is_(None))
        elif f.strategy:
            query = query.where(Position.strategy_type == f.strategy)
        for p in session.scalars(query).all():
            when = p.closed_at or p.created_at
            if not _in_range(when.date() if when else None, f):
                continue
            legs = [leg for leg in p.legs if leg.instrument_type == InstrumentType.OPTION]
            stock = _stock_leg(p)
            rows.append(
                {
                    "_when": when,
                    "kind": p.status.value,
                    "id": f"p{p.id}",
                    "date": _iso(when),
                    "opened_at": _iso(p.opened_at),
                    "underlying": p.underlying,
                    "strategy": p.strategy_type.value if p.strategy_type else "shares",
                    "expiration": _iso(legs[0].expiration) if legs else None,
                    "strikes": [float(leg.strike) for leg in legs if leg.strike is not None],
                    "quantity": legs[0].quantity if legs else (stock.quantity if stock else 0),
                    "credit": _f(p.entry_credit),
                    "debit": _f(p.exit_debit),
                    "pnl": _f(p.realized_pnl),
                    "reason": p.exit_reason.value if p.exit_reason else None,
                    "note": None,
                    # Per share, positive when the fill was worse than the mid.
                    "entry_slippage": _diff(_f(p.entry_mid), _f(p.entry_credit)),
                    "exit_slippage": _diff(_f(p.exit_debit), _f(p.exit_mid)),
                }
            )

    opp_statuses = []
    if "rejected" in kinds:
        opp_statuses.append(OpportunityStatus.REJECTED)
    if "ignored" in kinds:
        opp_statuses.append(OpportunityStatus.EXPIRED)
    if opp_statuses and f.strategy != "shares":
        query = (
            select(Opportunity, Decision, ShadowOutcome)
            .options(selectinload(Opportunity.legs))
            .outerjoin(Decision, Decision.opportunity_id == Opportunity.id)
            .outerjoin(ShadowOutcome, ShadowOutcome.opportunity_id == Opportunity.id)
            .where(Opportunity.status.in_(opp_statuses))
        )
        if f.underlying:
            query = query.where(Opportunity.underlying == f.underlying.upper())
        if f.strategy:
            query = query.where(Opportunity.strategy_type == f.strategy)
        for o, decision, shadow in session.execute(query).all():
            when = decision.decided_at if decision else o.created_at
            if not _in_range(when.date() if when else None, f):
                continue
            quantity = o.legs[0].quantity if o.legs else 1
            rows.append(
                {
                    "_when": when,
                    "kind": "rejected" if o.status == OpportunityStatus.REJECTED else "ignored",
                    "id": f"o{o.id}",
                    "date": _iso(when),
                    "opened_at": None,
                    "underlying": o.underlying,
                    "strategy": o.strategy_type.value,
                    "expiration": _iso(o.expiration),
                    "strikes": [
                        float(leg.strike)
                        for leg in sorted(o.legs, key=lambda leg: leg.side != Side.SELL)
                    ],
                    "quantity": quantity,
                    "credit": float(o.credit),
                    "debit": None,
                    # What the deal would have made if taken, once the shadow tracker resolves it.
                    "pnl": _f(shadow.pnl) if shadow and shadow.resolved_at else None,
                    "reason": (
                        decision.reject_reason.value
                        if decision and decision.reject_reason
                        else None
                    ),
                    "note": decision.note if decision else None,
                    "entry_slippage": None,
                    "exit_slippage": None,
                }
            )

    rows.sort(key=lambda r: r["_when"].timestamp(), reverse=True)
    for row in rows:
        del row["_when"]
    return rows[:limit]


def history_underlyings(session: Session) -> list[str]:
    """Tickers that appear in the log, for the filter's drop-down."""
    query = select(Position.underlying).union(
        select(Opportunity.underlying).where(
            or_(
                Opportunity.status == OpportunityStatus.REJECTED,
                Opportunity.status == OpportunityStatus.EXPIRED,
            )
        )
    )
    return sorted(set(session.scalars(query).all()))
