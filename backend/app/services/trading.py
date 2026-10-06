"""Order lifecycle on the broker: accepting a deal, order sync, automatic exits and the wheel.

Every order row is committed before it is sent, with a unique client order id, so a crash or a
network error between the two never loses or doubles an order: the next sync finds it at the
broker by that id, or sends it again.
"""

import logging
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker import ASSIGNMENT, EXPIRATION, Activity, Broker, BrokerError, BrokerOrder
from app.domain import exits
from app.domain.exits import (
    TRIGGER_LABELS,
    ExitSignal,
    MarketView,
    ShortPremium,
    evaluate_exit,
    stop_reached,
    take_profit_price,
)
from app.domain.orders import (
    OrderRequest,
    Quote,
    close_order,
    cost_to_close,
    net_credit,
    open_order,
    price,
    price_up,
    progressive_limit,
    realized_pnl,
)
from app.domain.params import StrategyParams
from app.domain.risk import NO_TRADE_MESSAGES, Exposure, Portfolio, check_limits
from app.models import (
    Decision,
    Fill,
    Opportunity,
    Order,
    Position,
    PositionEvent,
    PositionLeg,
    PositionMark,
    StrategyConfig,
)
from app.models.enums import (
    DecisionAction,
    ExitReason,
    InstrumentType,
    OpportunityStatus,
    OrderPurpose,
    OrderStatus,
    PositionEventType,
    PositionStatus,
    RejectReason,
    Side,
    StrategyType,
)
from app.services.safety import (
    MONITOR,
    entry_gate,
    losses,
    open_exposures,
    realized_capital,
    record_equity,
    record_job,
    share_lot_risk,
)
from app.services.screening import active_config, share_lots

logger = logging.getLogger(__name__)

LIVE = (OrderStatus.NEW, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED)
EXIT_PURPOSE = {
    exits.STOP_LOSS: OrderPurpose.STOP_LOSS,
    exits.TIME_EXIT: OrderPurpose.TIME_EXIT,
    exits.PROFIT_TARGET: OrderPurpose.TAKE_PROFIT,
}
EXIT_REASON = {
    OrderPurpose.TAKE_PROFIT: ExitReason.PROFIT_TARGET,
    OrderPurpose.STOP_LOSS: ExitReason.STOP_LOSS,
    OrderPurpose.TIME_EXIT: ExitReason.TIME_EXIT,
    OrderPurpose.MANUAL_CLOSE: ExitReason.MANUAL,
}
# A stop or time exit re-priced within this many re-pricing intervals continues its steps
# toward the natural price; after a longer pause it starts again one step past the mid.
EXIT_STEP_MEMORY = 3


class DecisionError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


def _dec(value: float, places: int = 4) -> Decimal:
    return Decimal(str(round(value, places)))


def _event(position: Position, kind: PositionEventType, **payload: Any) -> None:
    position.events.append(PositionEvent(type=kind, payload=payload or None, occurred_at=_now()))


def _option_legs(position: Position) -> list[PositionLeg]:
    return [leg for leg in position.legs if leg.instrument_type == InstrumentType.OPTION]


def _leg_sides(position: Position) -> list[tuple[str, str]]:
    return [(leg.symbol, leg.side.value) for leg in _option_legs(position)]


def _contracts(position: Position) -> int:
    return _option_legs(position)[0].quantity


def _short_leg(position: Position) -> PositionLeg | None:
    return next((leg for leg in _option_legs(position) if leg.side == Side.SELL), None)


def opening_quotes(
    legs: Sequence[tuple[str, str]], quotes: dict[str, Quote]
) -> tuple[float, float, float] | None:
    """Net credit per share of selling the position: (mid, natural, far); None if a leg has no
    usable quote. The natural sells at the bids and buys at the asks."""
    mid = natural = far = 0.0
    for symbol, side in legs:
        quote = quotes.get(symbol)
        if quote is None or quote.ask <= 0:
            return None
        sign = 1 if side == Side.SELL else -1
        mid += sign * quote.mid
        natural += quote.bid if sign > 0 else -quote.ask
        far += quote.ask if sign > 0 else -quote.bid
    return mid, natural, far


def position_params(session: Session, position: Position) -> StrategyParams:
    config = None
    if position.opportunity_id is not None:
        opportunity = session.get(Opportunity, position.opportunity_id)
        config = session.get(StrategyConfig, opportunity.strategy_config_id)
    return StrategyParams.from_dict((config or active_config(session)).params)


def _live_orders(session: Session, position: Position) -> list[Order]:
    query = select(Order).where(Order.position_id == position.id, Order.status.in_(LIVE))
    return list(session.scalars(query).all())


# --- decisions ------------------------------------------------------------------------------


def _existing_decision(session: Session, opportunity_id: int, key: str) -> Decision | None:
    decision = session.scalar(select(Decision).where(Decision.idempotency_key == key))
    if decision is not None and decision.opportunity_id != opportunity_id:
        raise DecisionError("Clé d'idempotence déjà utilisée pour une autre opportunité.")
    return decision


def _proposed(session: Session, opportunity_id: int) -> Opportunity:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise DecisionError("Opportunité introuvable.", 404)
    if opportunity.status != OpportunityStatus.PROPOSED:
        raise DecisionError(f"Opportunité déjà traitée ({opportunity.status.value}).")
    return opportunity


def accept_opportunity(
    session: Session,
    broker: Broker,
    opportunity_id: int,
    idempotency_key: str,
    starting_capital: float,
    limit_price: float | None = None,
) -> Position:
    """Record the acceptance, open a pending position and send the opening order.

    The limit is the screener's mid credit unless `limit_price` overrides it. Nothing is sent
    while an entry check fails: the safety gate (worker, monitor, data, broker, market, loss
    limits), fresh quotes for every leg close to the proposal, and the portfolio limits with
    the open and pending positions (other deals may have been accepted since the screening).
    """
    decision = _existing_decision(session, opportunity_id, idempotency_key)
    if decision is not None:
        if decision.action != DecisionAction.ACCEPT:
            raise DecisionError("Cette opportunité a été rejetée.")
        return session.scalars(
            select(Position).where(Position.opportunity_id == opportunity_id)
        ).one()

    opportunity = _proposed(session, opportunity_id)
    quantity = opportunity.legs[0].quantity
    fresh = _check_entry(session, broker, opportunity, starting_capital)
    parent_id = None
    if opportunity.strategy_type == StrategyType.COVERED_CALL:
        lot = next(
            (
                lot
                for lot in share_lots(session)
                if lot.underlying == opportunity.underlying and lot.shares >= 100 * quantity
            ),
            None,
        )
        if lot is None:
            raise DecisionError("Plus assez d'actions non couvertes pour ce covered call.")
        parent_id = lot.position_id

    session.add(
        Decision(
            opportunity_id=opportunity.id,
            action=DecisionAction.ACCEPT,
            idempotency_key=idempotency_key,
            decided_at=_now(),
        )
    )
    opportunity.status = OpportunityStatus.ACCEPTED
    position = Position(
        opportunity_id=opportunity.id,
        parent_position_id=parent_id,
        underlying=opportunity.underlying,
        sector=opportunity.sector,
        strategy_type=opportunity.strategy_type,
        status=PositionStatus.PENDING,
        collateral=opportunity.collateral,
        max_loss=opportunity.max_loss,
        stress_loss=opportunity.stress_loss,
        legs=[
            PositionLeg(
                instrument_type=InstrumentType.OPTION,
                symbol=leg.option_symbol,
                option_type=leg.option_type,
                strike=leg.strike,
                expiration=opportunity.expiration,
                side=leg.side,
                quantity=quantity,
            )
            for leg in opportunity.legs
        ],
    )
    session.add(position)
    session.flush()

    # Net quotes of the strategy at acceptance (at screening if none came back), to measure
    # the fill against the mid and the natural price.
    if fresh is None:
        legs = [(leg.option_symbol, leg.side.value) for leg in opportunity.legs]
        fresh = opening_quotes(
            legs,
            {leg.option_symbol: Quote(float(leg.bid), float(leg.ask)) for leg in opportunity.legs},
        )
    mid, natural, far = fresh if fresh is not None else (float(opportunity.credit),) * 3
    position.entry_mid = _dec(mid)
    position.entry_natural = _dec(natural)
    # Progressive limit from the screened mid; a price set by hand is never moved.
    credit = limit_price if limit_price is not None else float(opportunity.credit)
    order = _new_order(position, OrderPurpose.OPEN, credit, "day", natural, far)
    order.reprice_step = None if limit_price is not None else 0
    session.commit()
    submit_order(session, broker, order)
    session.commit()
    return position


def _check_entry(
    session: Session, broker: Broker, opportunity: Opportunity, starting_capital: float
) -> tuple[float, float, float] | None:
    """Raise a DecisionError listing every reason the entry cannot be sent now.

    Returns the fresh net credit quotes (mid, natural, far) of the deal.
    """
    params = StrategyParams.from_dict(active_config(session).params)
    covered_call = opportunity.strategy_type == StrategyType.COVERED_CALL
    market_open, broker_error, quotes = None, None, {}
    symbols = [leg.option_symbol for leg in opportunity.legs]
    try:
        market_open = broker.market_is_open()
        quotes = broker.option_quotes(symbols)
    except BrokerError as exc:
        broker_error = str(exc)[:200]
    gate = entry_gate(
        session,
        params,
        starting_capital,
        market_open=market_open,
        broker_error=broker_error,
        risk_increasing=not covered_call,
    )
    reasons = list(gate.reasons)

    fresh = None
    if broker_error is None:
        missing = [s for s in symbols if s not in quotes or quotes[s].ask <= 0]
        if missing:
            reasons.append(f"Donnée absente : pas de cotation pour {', '.join(missing)}")
        else:
            legs = [(leg.option_symbol, leg.side.value) for leg in opportunity.legs]
            fresh = opening_quotes(legs, quotes)
            mid = fresh[0]
            proposed = float(opportunity.credit)
            if mid < proposed * (1 - params.max_credit_drift_pct):
                reasons.append(
                    f"Données obsolètes : crédit coté {mid:.2f} contre {proposed:.2f} proposé "
                    f"(écart max {params.max_credit_drift_pct:.0%})"
                )

    if not covered_call:
        exposure = Exposure(
            underlying=opportunity.underlying,
            sector=opportunity.sector,
            strategy=opportunity.strategy_type.value,
            max_loss=float(opportunity.max_loss),
            stress_loss=float(
                opportunity.stress_loss
                if opportunity.stress_loss is not None
                else opportunity.max_loss
            ),
            collateral=float(opportunity.collateral),
            expiration=opportunity.expiration,
        )
        book = Portfolio(
            realized_capital(session, starting_capital), tuple(open_exposures(session, params))
        )
        reasons += [NO_TRADE_MESSAGES[code] for code in check_limits(exposure, book, params)]

    if reasons:
        raise DecisionError("Entrée bloquée : " + " ; ".join(reasons) + ".")
    return fresh


def reject_opportunity(
    session: Session,
    opportunity_id: int,
    idempotency_key: str,
    reason: RejectReason,
    note: str | None = None,
) -> Decision:
    decision = _existing_decision(session, opportunity_id, idempotency_key)
    if decision is not None:
        if decision.action != DecisionAction.REJECT:
            raise DecisionError("Cette opportunité a déjà été acceptée.")
        return decision
    opportunity = _proposed(session, opportunity_id)
    decision = Decision(
        opportunity_id=opportunity.id,
        action=DecisionAction.REJECT,
        reject_reason=reason,
        note=note,
        idempotency_key=idempotency_key,
        decided_at=_now(),
    )
    session.add(decision)
    opportunity.status = OpportunityStatus.REJECTED
    session.commit()
    return decision


# --- orders ---------------------------------------------------------------------------------


def _new_order(
    position: Position,
    purpose: OrderPurpose,
    limit_price: float,
    time_in_force: str,
    quote_bid: float | None = None,
    quote_ask: float | None = None,
) -> Order:
    order = Order(
        idempotency_key=f"ta-{purpose.value}-{position.id}-{uuid.uuid4().hex[:10]}",
        purpose=purpose,
        status=OrderStatus.NEW,
        order_type="limit",
        time_in_force=time_in_force,
        limit_price=_dec(limit_price, 2),
        quote_bid=None if quote_bid is None else _dec(quote_bid),
        quote_ask=None if quote_ask is None else _dec(quote_ask),
    )
    # Appended from the position's side: SQLAlchemy 2 no longer cascades a backref
    # assignment into the session.
    position.orders.append(order)
    return order


def order_request(order: Order) -> OrderRequest:
    """Rebuild the broker request from the stored order, so a failed send can be retried."""
    position = order.position
    legs = _leg_sides(position)
    limit = float(order.limit_price)
    if order.purpose == OrderPurpose.OPEN:
        return open_order(order.idempotency_key, legs, _contracts(position), limit)
    return close_order(
        order.idempotency_key, legs, _contracts(position), limit, order.time_in_force
    )


def submit_order(session: Session, broker: Broker, order: Order) -> None:
    """Send a NEW order. A transient failure leaves it NEW for the next sync to resolve."""
    try:
        result = broker.submit_order(order_request(order))
    except BrokerError as exc:
        if exc.transient:
            logger.warning("order %s not sent yet: %s", order.idempotency_key, exc)
            return
        logger.error("order %s rejected: %s", order.idempotency_key, exc)
        order.status = OrderStatus.REJECTED
        _event(
            order.position,
            PositionEventType.ORDER_REJECTED,
            order=order.idempotency_key,
            purpose=order.purpose.value,
            message=str(exc)[:300],
        )
        if order.purpose == OrderPurpose.OPEN:
            _never_opened(order.position)
        elif order.purpose == OrderPurpose.MANUAL_CLOSE:
            _restore_target(session, broker, order.position)
        return
    order.broker_order_id = result.id
    order.submitted_at = _now()
    order.status = OrderStatus.SUBMITTED
    apply_broker_order(session, broker, order, result)


def apply_broker_order(session: Session, broker: Broker, order: Order, result: BrokerOrder) -> None:
    """Move the order to the broker's state; on a final state, update the position."""
    status = OrderStatus(result.status)
    if status in LIVE:
        order.status = status
        return
    order.status = status
    position = order.position
    if result.filled_quantity > 0:
        filled_at = result.filled_at or _now()
        for fill in result.fills:
            order.fills.append(
                Fill(
                    symbol=fill.symbol,
                    side=Side(fill.side),
                    quantity=fill.quantity,
                    price=_dec(fill.price),
                    filled_at=filled_at,
                )
            )
    if order.purpose == OrderPurpose.OPEN:
        if result.filled_quantity > 0:
            _opened(session, broker, order, result)
        elif position.status == PositionStatus.PENDING and not order.replaced:
            # A step of the progressive limit is canceled to be sent again: still pending.
            _never_opened(position)
    elif result.filled_quantity > 0 and position.status == PositionStatus.OPEN:
        _closed(session, broker, order, result)
    if order.purpose == OrderPurpose.MANUAL_CLOSE:
        _restore_target(session, broker, position)


def _fill_price(order: Order, result: BrokerOrder) -> float:
    """Net credit per share of the fill (negative for a debit)."""
    if result.fills:
        return net_credit([(f.side, f.price) for f in result.fills])
    limit = float(order.limit_price)
    return limit if order.purpose == OrderPurpose.OPEN else -limit


def _opened(session: Session, broker: Broker, order: Order, result: BrokerOrder) -> None:
    position = order.position
    planned = _contracts(position)
    filled = min(result.filled_quantity, planned)
    prices = {f.symbol: f.price for f in result.fills}
    for leg in _option_legs(position):
        leg.quantity = filled
        if leg.symbol in prices:
            leg.avg_price = _dec(prices[leg.symbol])
    if filled < planned:
        ratio = Decimal(filled) / Decimal(planned)
        position.collateral = position.collateral * ratio
        if position.max_loss is not None:
            position.max_loss = position.max_loss * ratio
    # Rounded first: 2.05 - 1.00 is 1.0499999... in floating point, which would put the
    # profit target a cent below the one computed from the stored credit.
    credit = round(_fill_price(order, result), 4)
    position.entry_credit = _dec(credit)
    position.status = PositionStatus.OPEN
    position.opened_at = result.filled_at or _now()
    _event(position, PositionEventType.OPENED, credit=round(credit, 4), contracts=filled)

    params = position_params(session, position)
    if not params.use_take_profit:
        return
    target = take_profit_price(credit, params)
    take_profit = _new_order(position, OrderPurpose.TAKE_PROFIT, target, "gtc")
    session.flush()
    submit_order(session, broker, take_profit)
    _event(position, PositionEventType.TAKE_PROFIT_PLACED, price=target)


def _never_opened(position: Position) -> None:
    position.status = PositionStatus.CANCELED
    position.closed_at = _now()


def _closed(session: Session, broker: Broker, order: Order, result: BrokerOrder) -> None:
    position = order.position
    held = _contracts(position)
    filled = min(result.filled_quantity, held)
    debit = round(-_fill_price(order, result), 4)
    pnl = realized_pnl(float(position.entry_credit or 0), debit, filled)
    position.realized_pnl = (position.realized_pnl or Decimal("0")) + _dec(pnl, 2)
    if filled < held:
        # Partial close: the rest stays open and the monitor keeps watching it.
        ratio = Decimal(held - filled) / Decimal(held)
        for leg in _option_legs(position):
            leg.quantity = held - filled
        position.collateral = position.collateral * ratio
        if position.max_loss is not None:
            position.max_loss = position.max_loss * ratio
        return
    position.exit_debit = _dec(debit)
    position.exit_reason = EXIT_REASON[order.purpose]
    position.status = PositionStatus.CLOSED
    position.closed_at = result.filled_at or _now()
    _event(
        position,
        PositionEventType.CLOSED,
        reason=position.exit_reason.value,
        debit=round(debit, 4),
        pnl=float(position.realized_pnl),
    )
    others = [o for o in _live_orders(session, position) if o.id != order.id]
    _cancel(session, broker, others)


def _cancel(session: Session, broker: Broker, orders: Sequence[Order]) -> bool:
    """Cancel live orders and confirm it; False while one is still working at the broker."""
    done = True
    for order in orders:
        if order.broker_order_id is None:
            found = broker.find_order(order.idempotency_key)
            if found is None:
                order.status = OrderStatus.CANCELED
                continue
            order.broker_order_id = found.id
        try:
            broker.cancel_order(order.broker_order_id)
        except BrokerError as exc:
            if exc.status != 422:  # 422: no longer cancelable, most likely just filled
                raise
        for attempt in range(3):
            apply_broker_order(session, broker, order, broker.get_order(order.broker_order_id))
            if order.status not in LIVE:
                break
            time.sleep(1 + attempt)
        done = done and order.status not in LIVE
    return done


def sync_orders(session: Session, broker: Broker) -> None:
    """Bring every live order up to date with the broker, and send the ones never sent."""
    for order in session.scalars(select(Order).where(Order.status.in_(LIVE))).all():
        try:
            if order.broker_order_id is None:
                found = broker.find_order(order.idempotency_key)
                if found is None:
                    submit_order(session, broker, order)
                    continue
                order.broker_order_id = found.id
                order.submitted_at = order.submitted_at or _now()
                result = found
            else:
                result = broker.get_order(order.broker_order_id)
            apply_broker_order(session, broker, order, result)
        except BrokerError as exc:
            logger.warning("sync of order %s failed: %s", order.idempotency_key, exc)


# --- automatic exits ------------------------------------------------------------------------


def _confirmations(
    session: Session, position: Position, credit: float, params: StrategyParams
) -> int:
    """Consecutive latest marks (the one just added included) whose buy-back reached the stop."""
    rows = session.scalars(
        select(PositionMark)
        .where(PositionMark.position_id == position.id)
        .order_by(PositionMark.marked_at.desc(), PositionMark.id.desc())
        .limit(max(params.stop_confirmations, 1))
    ).all()
    count = 0
    for row in rows:
        natural = float(row.natural) if row.natural is not None else None
        if not stop_reached(float(row.mark), natural, credit, params):
            break
        count += 1
    return count


def _next_event(session: Session, underlying: str) -> date | None:
    """Next earnings date of the underlying, from its latest screening (None when unknown)."""
    return session.scalar(
        select(Opportunity.next_earnings)
        .where(Opportunity.underlying == underlying)
        .order_by(Opportunity.id.desc())
        .limit(1)
    )


def _drawdown_reached(session: Session, starting_capital: float | None, today: date) -> bool:
    if starting_capital is None:
        return False
    params = StrategyParams.from_dict(active_config(session).params)
    return losses(session, starting_capital, today).drawdown >= params.max_drawdown_pct


def check_exits(
    session: Session, broker: Broker, today: date, starting_capital: float | None = None
) -> None:
    """Mark every open option position and act on the exit rules.

    The profit target rests at the broker as a GTC order. A stop or a time exit cancels it
    first (both filling would leave a naked long), then buys back with a progressive limit.
    Each mark keeps the mid, the natural price and the underlying price. When the drawdown
    limit is reached (needs `starting_capital`), the losing position with the largest loss
    gets the portfolio stop signal on this pass.
    """
    positions = session.scalars(
        select(Position).where(
            Position.status == PositionStatus.OPEN, Position.strategy_type.is_not(None)
        )
    ).all()
    if not positions:
        return
    symbols = [leg.symbol for p in positions for leg in _option_legs(p)]
    quotes = broker.option_quotes(symbols)
    try:
        spots = broker.stock_prices(sorted({p.underlying for p in positions}))
    except BrokerError as exc:
        logger.warning("underlying prices unavailable: %s", exc)
        spots = {}
    now = _now()
    marked = []
    for position in positions:
        legs = _leg_sides(position)
        mark = cost_to_close(legs, quotes)
        if mark is None or position.entry_credit is None:
            continue
        natural = cost_to_close(legs, quotes, natural=True)
        credit = float(position.entry_credit)
        short = _short_leg(position)
        delta = quotes[short.symbol].delta if short is not None else None
        spot = spots.get(position.underlying)
        session.add(
            PositionMark(
                position_id=position.id,
                marked_at=now,
                mark=_dec(mark),
                natural=None if natural is None else _dec(natural),
                delta=None if delta is None else _dec(delta, 6),
                underlying_price=None if spot is None else _dec(spot),
                unrealized_pnl=_dec(realized_pnl(credit, mark, _contracts(position)), 2),
            )
        )
        params = position_params(session, position)
        # A True Wheel put (assignment accepted by the version it was opened with) is never
        # stopped or exited at 21 DTE; puts opened before the True Wheel existed keep both.
        accepted = position.strategy_type == StrategyType.CASH_SECURED_PUT and (
            params.assignment_accepted(position.underlying)
        )
        marked.append((position, params, accepted, mark, natural, delta, spot))
    session.flush()

    worst = None
    if _drawdown_reached(session, starting_capital, today):
        losing = [
            (realized_pnl(float(p.entry_credit), mark, _contracts(p)), p.id)
            for p, _, accepted, mark, *_ in marked
            if not accepted
            and p.strategy_type != StrategyType.COVERED_CALL
            and mark > float(p.entry_credit)
        ]
        worst = min(losing)[1] if losing else None

    for position, params, accepted, mark, natural, delta, spot in marked:
        credit = float(position.entry_credit)
        short = _short_leg(position)
        expiration = _option_legs(position)[0].expiration
        view = MarketView(
            natural=natural,
            confirmations=_confirmations(session, position, credit, params),
            short_delta=delta,
            spot=spot,
            short_strike=float(short.strike) if short is not None and short.strike else None,
            next_event=_next_event(session, position.underlying),
            portfolio_breach=position.id == worst,
        )
        signal = evaluate_exit(
            ShortPremium(position.strategy_type.value, credit, expiration, accepted),
            mark,
            today,
            params,
            view,
        )
        try:
            if signal is None:
                if _between_steps(position) and not _live_orders(session, position):
                    # A stop step canceled for re-pricing, and the price came back.
                    _restore_target(session, broker, position)
                continue
            _act_on_exit(session, broker, position, signal, natural, params, now)
        except BrokerError as exc:
            logger.warning("exit of position %s failed: %s", position.id, exc)


def _between_steps(position: Position) -> bool:
    """True when the last order sent is a stop or time exit canceled to be re-priced."""
    last = max(position.orders, key=lambda o: o.id or 0, default=None)
    return (
        last is not None
        and last.purpose in (OrderPurpose.STOP_LOSS, OrderPurpose.TIME_EXIT)
        and last.replaced
    )


def _exit_step(position: Position, params: StrategyParams, now: datetime) -> int:
    """Step of the next stop or time exit order: one past the last re-priced one, if recent."""
    exits_sent = [
        o
        for o in position.orders
        if o.purpose in (OrderPurpose.STOP_LOSS, OrderPurpose.TIME_EXIT)
        and o.reprice_step is not None
    ]
    last = max(exits_sent, key=lambda o: o.id or 0, default=None)
    memory = timedelta(minutes=params.exit_reprice_minutes * EXIT_STEP_MEMORY)
    if last is None or not last.replaced or last.submitted_at is None:
        return 1
    if now - last.submitted_at > memory:
        return 1
    return last.reprice_step + 1


def _act_on_exit(
    session: Session,
    broker: Broker,
    position: Position,
    signal: ExitSignal,
    natural: float | None,
    params: StrategyParams,
    now: datetime,
) -> None:
    reason, mark = signal.reason, signal.mark
    credit = float(position.entry_credit or 0)
    live = _live_orders(session, position)
    working = [o for o in live if o.purpose != OrderPurpose.TAKE_PROFIT]
    if working:
        after = timedelta(minutes=params.exit_reprice_minutes)
        repriceable = (OrderPurpose.STOP_LOSS, OrderPurpose.TIME_EXIT)
        if any(
            o.purpose not in repriceable or not o.submitted_at or now - o.submitted_at < after
            for o in working
        ):
            return  # a manual buy-back, or a step still within its interval
        for order in working:
            order.replaced = True
        if not _cancel(session, broker, working) or position.status != PositionStatus.OPEN:
            return  # still working, or filled meanwhile
        live = _live_orders(session, position)  # sent again one step further below
    resting_target = [o for o in live if o.purpose == OrderPurpose.TAKE_PROFIT]
    step = None
    if reason == exits.PROFIT_TARGET:
        if resting_target:
            return  # the GTC order is working
        limit, time_in_force = take_profit_price(credit, params), "gtc"
    else:
        if not _cancel(session, broker, resting_target) or position.status != PositionStatus.OPEN:
            return
        step = _exit_step(position, params, now)
        target = (
            mark if natural is None else progressive_limit(mark, natural, step, params.limit_steps)
        )
        limit, time_in_force = price_up(target), "day"
        payload: dict[str, Any] = {
            "mark": round(mark, 4),
            "natural": None if natural is None else round(natural, 4),
            "limit": limit,
            "step": step,
        }
        if step == 1:
            position.exit_mid = _dec(mark)
            position.exit_natural = None if natural is None else _dec(natural)
            kind = (
                PositionEventType.STOP_TRIGGERED
                if reason == exits.STOP_LOSS
                else PositionEventType.TIME_EXIT_TRIGGERED
            )
            if reason == exits.STOP_LOSS:
                payload["triggers"] = list(signal.triggers)
                payload["labels"] = [TRIGGER_LABELS[t] for t in signal.triggers]
            _event(position, kind, **payload)
        else:
            _event(position, PositionEventType.ORDER_REPRICED, purpose=reason, **payload)
    order = _new_order(position, EXIT_PURPOSE[reason], limit, time_in_force)
    order.reprice_step = step
    session.flush()
    submit_order(session, broker, order)


# --- progressive entries ----------------------------------------------------------------------


def reprice_entries(session: Session, broker: Broker, now: datetime | None = None) -> None:
    """Move unfilled opening orders one step closer to the natural credit.

    A step older than `entry_reprice_minutes` is canceled and sent again 1/`limit_steps` of
    the way from the current mid to the current natural credit, never below the proposed
    credit minus `max_credit_drift_pct`. A price set by hand is never moved; the last step
    stays until the end of the day (day order).
    """
    now = now or _now()
    pending = session.scalars(
        select(Position).where(
            Position.status == PositionStatus.PENDING, Position.strategy_type.is_not(None)
        )
    ).all()
    if not pending:
        return
    quotes = broker.option_quotes([leg.symbol for p in pending for leg in _option_legs(p)])
    for position in pending:
        try:
            _reprice_entry(session, broker, position, quotes, now)
        except BrokerError as exc:
            logger.warning("re-pricing of position %s failed: %s", position.id, exc)


def _reprice_entry(
    session: Session,
    broker: Broker,
    position: Position,
    quotes: dict[str, Quote],
    now: datetime,
) -> None:
    opens = [o for o in position.orders if o.purpose == OrderPurpose.OPEN]
    last = max(opens, key=lambda o: o.id or 0, default=None)
    if last is None or last.reprice_step is None:
        return
    params = position_params(session, position)
    if last.status in LIVE:
        after = timedelta(minutes=params.entry_reprice_minutes)
        if last.submitted_at is None or now - last.submitted_at < after:
            return
        if last.reprice_step >= params.limit_steps:
            return
    elif last.status == OrderStatus.CANCELED and last.replaced:
        # Canceled for a new step that was never sent: give up after the session.
        if last.submitted_at is not None and now - last.submitted_at > timedelta(hours=8):
            _never_opened(position)
            return
    else:
        return
    net = opening_quotes(_leg_sides(position), quotes)
    if net is None:
        return  # no fresh quote: the working step stays
    mid, natural, far = net
    step = last.reprice_step + 1
    opportunity = session.get(Opportunity, position.opportunity_id)
    floor = float(opportunity.credit) * (1 - params.max_credit_drift_pct)
    limit = price(max(progressive_limit(mid, natural, step, params.limit_steps), floor))
    if last.status in LIVE:
        if limit >= float(last.limit_price):
            return  # the working order already asks no more than the next step
        last.replaced = True
        if not _cancel(session, broker, [last]) or position.status != PositionStatus.PENDING:
            return  # still working, or filled while being canceled
    order = _new_order(position, OrderPurpose.OPEN, limit, "day", natural, far)
    order.reprice_step = step
    _event(
        position,
        PositionEventType.ORDER_REPRICED,
        purpose="open",
        step=step,
        limit=limit,
        mark=round(mid, 4),
        natural=round(natural, 4),
    )
    session.flush()
    submit_order(session, broker, order)


# --- manual buy-back ------------------------------------------------------------------------


def close_position(
    session: Session, broker: Broker, position_id: int, idempotency_key: str
) -> Order:
    """Buy an open option position back now, at the natural price (the user's button).

    Same path as a stop: the resting profit target is canceled and confirmed first, so both
    can never fill. If the day order ends unfilled, the profit target is placed again.
    """
    key = f"ta-manual-{idempotency_key}"
    existing = session.scalar(select(Order).where(Order.idempotency_key == key))
    if existing is not None:
        if existing.position_id != position_id:
            raise DecisionError("Clé d'idempotence déjà utilisée pour une autre position.")
        return existing

    position = session.get(Position, position_id)
    if position is None:
        raise DecisionError("Position introuvable.", 404)
    if position.status != PositionStatus.OPEN or position.strategy_type is None:
        raise DecisionError("Seule une position d'options ouverte peut être rachetée.")
    live = _live_orders(session, position)
    if any(o.purpose != OrderPurpose.TAKE_PROFIT for o in live):
        raise DecisionError("Un ordre de rachat est déjà en cours sur cette position.")
    if not broker.market_is_open():
        raise DecisionError("Marché fermé : le rachat manuel n'est possible qu'en séance.")
    legs = _leg_sides(position)
    quotes = broker.option_quotes([symbol for symbol, _ in legs])
    natural = cost_to_close(legs, quotes, natural=True)
    if natural is None:
        raise DecisionError("Pas de cotation exploitable pour racheter cette position.", 503)
    if not _cancel(session, broker, [o for o in live if o.purpose == OrderPurpose.TAKE_PROFIT]):
        session.commit()
        raise DecisionError("L'ordre de prise de profit n'est pas encore annulé, réessaie.")
    if position.status != PositionStatus.OPEN:
        session.commit()  # the profit target filled while it was being canceled
        raise DecisionError("La position vient d'être clôturée par la prise de profit.")

    order = _new_order(position, OrderPurpose.MANUAL_CLOSE, price_up(natural), "day")
    order.idempotency_key = key
    position.exit_mid = _dec(cost_to_close(legs, quotes))
    position.exit_natural = _dec(natural)
    # Net quote of the buy-back: the far side (short legs at the bid) and the natural.
    far = sum(quotes[sym].bid if side == Side.SELL else -quotes[sym].ask for sym, side in legs)
    order.quote_bid = _dec(max(far, 0.0))
    order.quote_ask = _dec(natural)
    session.commit()
    submit_order(session, broker, order)
    session.commit()
    return order


def _restore_target(session: Session, broker: Broker, position: Position) -> None:
    """Place the GTC profit target again after a manual buy-back that did not fill."""
    if position.status != PositionStatus.OPEN or position.entry_credit is None:
        return
    if any(o.purpose == OrderPurpose.TAKE_PROFIT for o in _live_orders(session, position)):
        return
    params = position_params(session, position)
    if not params.use_take_profit:
        return
    target = take_profit_price(float(position.entry_credit), params)
    order = _new_order(position, OrderPurpose.TAKE_PROFIT, target, "gtc")
    session.flush()
    submit_order(session, broker, order)
    _event(position, PositionEventType.TAKE_PROFIT_PLACED, price=target)


# --- expiration and assignment (the wheel) --------------------------------------------------


def _handled_activities(session: Session) -> set[str]:
    kinds = (
        PositionEventType.EXPIRED,
        PositionEventType.ASSIGNED,
        PositionEventType.CALLED_AWAY,
        PositionEventType.RECONCILIATION_MISMATCH,
    )
    payloads = session.scalars(select(PositionEvent.payload).where(PositionEvent.type.in_(kinds)))
    return {p["activity"] for p in payloads if p and "activity" in p}


def sync_activities(session: Session, broker: Broker, today: date) -> None:
    """Apply expirations and assignments reported by the broker to the matching positions."""
    activities = broker.option_activities(today - timedelta(days=10))
    if not activities:
        return
    done = _handled_activities(session)
    legs: dict[str, PositionLeg] = {}
    for position in session.scalars(
        select(Position).where(Position.status == PositionStatus.OPEN)
    ).all():
        for leg in _option_legs(position):
            legs[leg.symbol] = leg
    for activity in activities:
        leg = legs.get(activity.symbol)
        if activity.id in done or leg is None or leg.position.status != PositionStatus.OPEN:
            continue
        position = leg.position
        if activity.kind == EXPIRATION:
            _expired(position, activity)
        elif activity.kind == ASSIGNMENT and leg.side == Side.SELL:
            if position.strategy_type in (StrategyType.CASH_SECURED_PUT, StrategyType.SHORT_PUT):
                _put_assigned(session, position, leg, activity)
            elif position.strategy_type == StrategyType.COVERED_CALL:
                _call_assigned(session, position, leg, activity)
            else:
                _mismatch(
                    position, activity, "assignation anticipée d'un spread : à traiter à la main"
                )
        else:
            _mismatch(position, activity, f"{activity.kind} inattendu sur {activity.symbol}")


def _mismatch(position: Position, activity: Activity, message: str) -> None:
    logger.error("position %s: %s", position.id, message)
    _event(
        position, PositionEventType.RECONCILIATION_MISMATCH, activity=activity.id, message=message
    )


def _expired(position: Position, activity: Activity) -> None:
    """Worthless expiration: the whole credit is kept."""
    credit = float(position.entry_credit or 0)
    pnl = realized_pnl(credit, 0.0, _contracts(position))
    position.realized_pnl = (position.realized_pnl or Decimal("0")) + _dec(pnl, 2)
    position.exit_debit = Decimal("0")
    position.exit_reason = ExitReason.EXPIRATION
    position.status = PositionStatus.EXPIRED
    position.closed_at = _now()
    _event(position, PositionEventType.EXPIRED, activity=activity.id, pnl=pnl)


def _put_assigned(
    session: Session, position: Position, leg: PositionLeg, activity: Activity
) -> None:
    """The put buys the shares at the strike: they become a share lot for covered calls.

    The premium lowers the cost basis (strike - credit) instead of being booked as profit, so
    the wheel's P&L is counted once, when the shares are called away or sold.
    """
    contracts = min(activity.quantity, leg.quantity)
    shares = 100 * contracts
    strike = leg.strike or Decimal("0")
    credit = position.entry_credit or Decimal("0")
    if contracts < leg.quantity:
        position.collateral = position.collateral * (leg.quantity - contracts) / leg.quantity
        leg.quantity -= contracts
    else:
        position.status = PositionStatus.ASSIGNED
        position.exit_reason = ExitReason.ASSIGNMENT
        position.exit_debit = Decimal("0")
        position.realized_pnl = position.realized_pnl or Decimal("0")
        position.closed_at = _now()
    params = position_params(session, position)
    cost = strike - credit
    lot = Position(
        parent_position_id=position.id,
        underlying=position.underlying,
        sector=position.sector,
        strategy_type=None,
        status=PositionStatus.OPEN,
        opened_at=_now(),
        collateral=strike * shares,
        # Contractual: the stock going to zero; stress: the stock gapping down.
        max_loss=cost * shares,
        stress_loss=_dec(share_lot_risk(float(cost), shares, params, position.underlying), 2),
        legs=[
            PositionLeg(
                instrument_type=InstrumentType.STOCK,
                symbol=position.underlying,
                side=Side.BUY,
                quantity=shares,
                avg_price=strike - credit,
            )
        ],
    )
    session.add(lot)
    session.flush()
    _event(
        position,
        PositionEventType.ASSIGNED,
        activity=activity.id,
        shares=shares,
        share_position=lot.id,
    )
    _event(lot, PositionEventType.OPENED, shares=shares, cost_basis=float(strike - credit))


def _call_assigned(
    session: Session, position: Position, leg: PositionLeg, activity: Activity
) -> None:
    """The call sells the shares at the strike: the call keeps its premium, the lot its gain."""
    contracts = min(activity.quantity, leg.quantity)
    shares = 100 * contracts
    strike = leg.strike or Decimal("0")
    credit = position.entry_credit or Decimal("0")
    position.realized_pnl = (position.realized_pnl or Decimal("0")) + credit * 100 * contracts
    if contracts < leg.quantity:
        leg.quantity -= contracts
    else:
        position.status = PositionStatus.ASSIGNED
        position.exit_reason = ExitReason.CALLED_AWAY
        position.exit_debit = Decimal("0")
        position.closed_at = _now()
    _event(position, PositionEventType.CALLED_AWAY, activity=activity.id, shares=shares)

    lot = (
        session.get(Position, position.parent_position_id) if position.parent_position_id else None
    )
    stock = (
        next((s for s in lot.legs if s.instrument_type == InstrumentType.STOCK), None)
        if lot
        else None
    )
    if lot is None or stock is None:
        _mismatch(position, activity, "covered call assigné sans lot d'actions rattaché")
        return
    cost = stock.avg_price or Decimal("0")
    lot.realized_pnl = (lot.realized_pnl or Decimal("0")) + (strike - cost) * shares
    remaining = max(stock.quantity - shares, 0)
    lot.collateral = lot.collateral * remaining / stock.quantity if stock.quantity else Decimal("0")
    stock.quantity = remaining
    if remaining == 0:
        lot.status = PositionStatus.CLOSED
        lot.exit_reason = ExitReason.CALLED_AWAY
        lot.closed_at = _now()
    _event(lot, PositionEventType.CALLED_AWAY, shares=shares, price=float(strike))


# --- the monitor job ------------------------------------------------------------------------


def monitor(
    session: Session, broker: Broker, today: date, starting_capital: float | None = None
) -> bool:
    """One pass of the position monitor. Exit rules only run while the market is open.

    A failing step is logged and rolled back without blocking the next ones. The pass is
    recorded (the entry gate needs a recent successful one), with the account value of the
    day when `starting_capital` is given. Returns False if a step failed.
    """
    state: dict[str, Any] = {"market_open": None}

    def exits_step() -> None:
        state["market_open"] = broker.market_is_open()
        if state["market_open"]:
            reprice_entries(session, broker)
            check_exits(session, broker, today, starting_capital)

    steps = [
        ("orders", lambda: sync_orders(session, broker)),
        ("activities", lambda: sync_activities(session, broker, today)),
        ("exits", exits_step),
    ]
    failed: list[str] = []
    broker_error = None
    for name, step in steps:
        try:
            step()
            session.commit()
        except Exception as exc:
            logger.exception("monitor step %s failed", name)
            session.rollback()
            failed.append(name)
            if isinstance(exc, BrokerError):
                broker_error = str(exc)[:200]
    if starting_capital is not None:
        try:
            record_equity(session, starting_capital, today)
        except Exception:
            logger.exception("account snapshot failed")
            session.rollback()
            failed.append("equity")
    record_job(
        session,
        MONITOR,
        ok=not failed,
        error=f"étapes en échec : {', '.join(failed)}" if failed else None,
        details={
            "market_open": state["market_open"],
            "broker_ok": broker_error is None and state["market_open"] is not None,
            "broker_error": broker_error,
            "failed_steps": failed,
        },
    )
    session.commit()
    return not failed
