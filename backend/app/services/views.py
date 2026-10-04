"""Read models for the interface: dashboard, today's deals, open positions and the trade log.

Amounts go out as floats (display only); every decision is taken on the stored decimals.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.params import StrategyParams
from app.models import (
    Decision,
    Opportunity,
    Order,
    Position,
    PositionLeg,
    PositionMark,
    ScreenerRun,
    ShadowOutcome,
)
from app.models.enums import (
    InstrumentType,
    OpportunityStatus,
    OrderPurpose,
    PositionStatus,
    Side,
)
from app.services.screening import ACTIVE, account_state, active_config
from app.services.trading import LIVE

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


def dashboard(session: Session, starting_capital: float) -> dict[str, Any]:
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
    return {
        "starting_capital": float(starting_capital),
        "realized_pnl": round(account.capital - float(starting_capital), 2),
        "capital": round(account.capital, 2),
        "engaged": round(account.engaged, 2),
        "available": round(account.capital - account.engaged, 2),
        "max_engaged_pct": params.max_engaged_pct,
        "max_trade_pct": params.max_trade_pct,
        "engagement_capacity": round(account.remaining_capacity(params), 2),
        "unrealized_pnl": round(sum(float(m.unrealized_pnl) for m in marks.values()), 2),
        "open_positions": counts.get(PositionStatus.OPEN, 0),
        "pending_positions": counts.get(PositionStatus.PENDING, 0),
        "proposed_opportunities": proposed or 0,
        "last_screener_run": _iso(last_run),
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
        "weight": round(float(o.collateral) / capital, 4) if capital > 0 else None,
        "breakeven": float(o.breakeven),
        "delta": float(o.short_delta),
        "pop": float(o.pop),
        # Return on risk: the credit over what can be lost.
        "ror": round(credit_total / max_loss, 4) if max_loss > 0 else None,
        "aroc": float(o.aroc),
        "iv_rank": _f(o.iv_rank),
        "iv_rank_method": metrics.get("iv_rank_method"),
        "score": float(o.score),
        "next_earnings": _iso(o.next_earnings),
        "take_profit_price": target,
        "take_profit_gain": (
            None if target is None else round((float(o.credit) - target) * 100 * quantity, 2)
        ),
        "stop_price": metrics.get("stop_price"),
        "time_exit_date": metrics.get("time_exit_date"),
    }


def opportunities(
    session: Session, starting_capital: float, status: OpportunityStatus | None
) -> list[dict[str, Any]]:
    capital = account_state(session, starting_capital).capital
    query = select(Opportunity).options(selectinload(Opportunity.legs))
    if status is not None:
        query = query.where(Opportunity.status == status)
    query = query.order_by(Opportunity.created_at.desc(), Opportunity.score.desc()).limit(50)
    return [_opportunity(o, capital) for o in session.scalars(query).all()]


# --- open positions -------------------------------------------------------------------------


def _orders_by_position(session: Session, ids: list[int]) -> dict[int, list[Order]]:
    rows: dict[int, list[Order]] = {}
    if ids:
        query = select(Order).where(Order.position_id.in_(ids), Order.status.in_(LIVE))
        for order in session.scalars(query).all():
            rows.setdefault(order.position_id, []).append(order)
    return rows


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
                "collateral": float(p.collateral),
                "max_loss": _f(p.max_loss),
                "mark": _f(mark.mark) if mark else None,
                "marked_at": _iso(mark.marked_at) if mark else None,
                "unrealized_pnl": _f(mark.unrealized_pnl) if mark else None,
                "profit_pct": profit_pct,
                "take_profit_price": _f(target.limit_price) if target else None,
                "exit_order": (
                    {"purpose": exit_order.purpose.value, "limit": _f(exit_order.limit_price)}
                    if exit_order
                    else None
                ),
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
