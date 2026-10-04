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
from app.domain.exits import ShortPremium, evaluate_exit, take_profit_price
from app.domain.orders import (
    OrderRequest,
    Quote,
    close_order,
    cost_to_close,
    net_credit,
    open_order,
    price_up,
    realized_pnl,
)
from app.domain.params import StrategyParams
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
from app.services.screening import account_state, active_config, open_underlyings, share_lots

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
# A stop or time exit that has not filled after this long is re-priced at the new natural.
EXIT_REPRICE_AFTER = timedelta(minutes=15)


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


def _params(session: Session, position: Position) -> StrategyParams:
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

    The limit is the screener's mid credit unless `limit_price` overrides it. Risk limits are
    checked again: other deals may have been accepted since the screener sized this one.
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
    else:
        if opportunity.underlying in open_underlyings(session):
            raise DecisionError(f"Une position est déjà ouverte sur {opportunity.underlying}.")
        config = session.get(StrategyConfig, opportunity.strategy_config_id)
        params = StrategyParams.from_dict(config.params)
        capacity = account_state(session, starting_capital).remaining_capacity(params)
        if float(opportunity.collateral) > capacity + 0.01:
            raise DecisionError(
                f"Capital insuffisant : {float(opportunity.collateral):.0f} requis, "
                f"{capacity:.0f} disponibles sous la limite d'engagement."
            )

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

    # Net natural and far quotes of the strategy at screening time, to measure fill quality.
    short_bid = sum(float(leg.bid) for leg in opportunity.legs if leg.side == Side.SELL)
    short_ask = sum(float(leg.ask) for leg in opportunity.legs if leg.side == Side.SELL)
    long_bid = sum(float(leg.bid) for leg in opportunity.legs if leg.side == Side.BUY)
    long_ask = sum(float(leg.ask) for leg in opportunity.legs if leg.side == Side.BUY)
    credit = limit_price if limit_price is not None else float(opportunity.credit)
    order = _new_order(
        position, OrderPurpose.OPEN, credit, "day", short_bid - long_ask, short_ask - long_bid
    )
    session.commit()
    submit_order(session, broker, order)
    session.commit()
    return position


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
        elif position.status == PositionStatus.PENDING:
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

    params = _params(session, position)
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


def check_exits(session: Session, broker: Broker, today: date) -> None:
    """Mark every open option position and act on the exit rules.

    The profit target rests at the broker as a GTC order. A stop or a time exit cancels it
    first (both filling would leave a naked long), then buys back at the natural price.
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
    now = _now()
    for position in positions:
        legs = _leg_sides(position)
        mark = cost_to_close(legs, quotes)
        if mark is None or position.entry_credit is None:
            continue
        credit = float(position.entry_credit)
        contracts = _contracts(position)
        session.add(
            PositionMark(
                position_id=position.id,
                marked_at=now,
                mark=_dec(mark),
                unrealized_pnl=_dec(realized_pnl(credit, mark, contracts), 2),
            )
        )
        params = _params(session, position)
        expiration = _option_legs(position)[0].expiration
        signal = evaluate_exit(
            ShortPremium(position.strategy_type.value, credit, expiration), mark, today, params
        )
        if signal is None:
            continue
        try:
            _act_on_exit(session, broker, position, signal.reason, mark, quotes, params, now)
        except BrokerError as exc:
            logger.warning("exit of position %s failed: %s", position.id, exc)


def _act_on_exit(
    session: Session,
    broker: Broker,
    position: Position,
    reason: str,
    mark: float,
    quotes: dict[str, Quote],
    params: StrategyParams,
    now: datetime,
) -> None:
    legs = _leg_sides(position)
    credit = float(position.entry_credit or 0)
    live = _live_orders(session, position)
    working = [o for o in live if o.purpose != OrderPurpose.TAKE_PROFIT]
    if working:
        stale = [o for o in working if o.submitted_at and now - o.submitted_at > EXIT_REPRICE_AFTER]
        _cancel(session, broker, stale)  # re-priced on the next pass
        return
    resting_target = [o for o in live if o.purpose == OrderPurpose.TAKE_PROFIT]
    if reason == exits.PROFIT_TARGET:
        if resting_target:
            return  # the GTC order is working
        limit, time_in_force = take_profit_price(credit, params), "gtc"
    else:
        if not _cancel(session, broker, resting_target) or position.status != PositionStatus.OPEN:
            return
        natural = cost_to_close(legs, quotes, natural=True)
        limit, time_in_force = price_up(natural if natural is not None else mark), "day"
        kind = (
            PositionEventType.STOP_TRIGGERED
            if reason == exits.STOP_LOSS
            else PositionEventType.TIME_EXIT_TRIGGERED
        )
        _event(position, kind, mark=round(mark, 4), limit=limit)
    order = _new_order(position, EXIT_PURPOSE[reason], limit, time_in_force)
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
    params = _params(session, position)
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
            if position.strategy_type == StrategyType.CASH_SECURED_PUT:
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
    lot = Position(
        parent_position_id=position.id,
        underlying=position.underlying,
        sector=position.sector,
        strategy_type=None,
        status=PositionStatus.OPEN,
        opened_at=_now(),
        collateral=strike * shares,
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


def monitor(session: Session, broker: Broker, today: date) -> None:
    """One pass of the position monitor. Exit rules only run while the market is open.

    A failing step is logged and rolled back without blocking the next ones.
    """
    steps = [
        ("orders", lambda: sync_orders(session, broker)),
        ("activities", lambda: sync_activities(session, broker, today)),
        ("exits", lambda: broker.market_is_open() and check_exits(session, broker, today)),
    ]
    for name, step in steps:
        try:
            step()
            session.commit()
        except Exception:
            logger.exception("monitor step %s failed", name)
            session.rollback()
