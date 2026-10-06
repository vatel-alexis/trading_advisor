from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker import ASSIGNMENT, EXPIRATION, Activity, BrokerError
from app.db import get_session
from app.domain.exits import take_profit_price
from app.domain.orders import Quote
from app.domain.params import StrategyParams
from app.main import app, get_broker
from app.models import Opportunity, OpportunityLeg, Order, Position, PositionMark, ScreenerRun
from app.models.enums import (
    ExitReason,
    InstrumentType,
    OpportunityStatus,
    OptionType,
    OrderPurpose,
    OrderStatus,
    PositionEventType,
    PositionStatus,
    RejectReason,
    Side,
    StrategyType,
)
from app.services.safety import MONITOR, record_job
from app.services.screening import active_config, share_lots
from app.services.trading import (
    DecisionError,
    accept_opportunity,
    check_exits,
    reject_opportunity,
    reprice_entries,
    sync_activities,
    sync_orders,
)
from tests.chains import TODAY
from tests.fake_broker import FakeBroker

PARAMS = StrategyParams()
EXPIRATION_DAY = TODAY + timedelta(days=40)
SHORT, LONG = "SPY261114P00500000", "SPY261114P00495000"
SOFI_PUT, SOFI_CALL = "SOFI261114P00015000", "SOFI261114C00016000"


def make_opportunity(
    session: Session,
    strategy: StrategyType,
    underlying: str,
    legs: list[tuple[str, OptionType, Side, float, float, float]],
    credit: float,
    quantity: int,
    collateral: float,
    stress_loss: float | None = None,
) -> Opportunity:
    """`legs` are (symbol, type, side, strike, bid, ask)."""
    config = active_config(session)
    now = datetime.now(UTC)
    run = ScreenerRun(strategy_config_id=config.id, started_at=now, finished_at=now)
    session.add(run)
    session.flush()
    opportunity = Opportunity(
        screener_run_id=run.id,
        strategy_config_id=config.id,
        underlying=underlying,
        sector="ETF" if underlying == "SPY" else "Financials",
        strategy_type=strategy,
        status=OpportunityStatus.PROPOSED,
        expiration=EXPIRATION_DAY,
        dte=40,
        underlying_price=Decimal("550"),
        credit=Decimal(str(credit)),
        max_loss=Decimal(str(collateral)),
        collateral=Decimal(str(collateral)),
        stress_loss=Decimal(str(collateral if stress_loss is None else stress_loss)),
        breakeven=Decimal("499"),
        short_delta=Decimal("-0.2"),
        pop=Decimal("0.8"),
        aroc=Decimal("0.3"),
        score=Decimal("0.5"),
        legs=[
            OpportunityLeg(
                option_symbol=symbol,
                option_type=option_type,
                side=side,
                strike=Decimal(str(strike)),
                quantity=quantity,
                bid=Decimal(str(bid)),
                ask=Decimal(str(ask)),
            )
            for symbol, option_type, side, strike, bid, ask in legs
        ],
    )
    session.add(opportunity)
    session.flush()
    return opportunity


def spread(session: Session) -> Opportunity:
    legs = [
        (SHORT, OptionType.PUT, Side.SELL, 500, 2.0, 2.1),
        (LONG, OptionType.PUT, Side.BUY, 495, 1.0, 1.1),
    ]
    return make_opportunity(session, StrategyType.PUT_CREDIT_SPREAD, "SPY", legs, 1.0, 2, 800)


def healthy(session: Session, broker: FakeBroker, opportunity_id: int | None = None) -> None:
    """A worker and a monitor that just ran, an open market and quotes for the deal's legs."""
    record_job(session, MONITOR, ok=True, details={"market_open": True, "broker_ok": True})
    broker.is_open = True
    if opportunity_id is not None:
        for leg in session.get(Opportunity, opportunity_id).legs:
            broker.quotes.setdefault(leg.option_symbol, Quote(float(leg.bid), float(leg.ask)))


def accept(
    session: Session,
    broker: FakeBroker,
    opportunity_id: int,
    key: str,
    capital: float = 20_000,
    limit_price: float | None = None,
) -> Position:
    """Accept with every entry check passing (the checks have their own tests)."""
    healthy(session, broker, opportunity_id)
    return accept_opportunity(session, broker, opportunity_id, key, capital, limit_price)


def open_spread(session: Session, broker: FakeBroker) -> Position:
    """Accept the spread and fill it at a 1.05 credit."""
    position = accept(session, broker, spread(session).id, "click-0001")
    order_id, _ = broker.last()
    broker.fill(order_id, {SHORT: 2.05, LONG: 1.0})
    sync_orders(session, broker)
    session.flush()
    return position


def orders(session: Session, position: Position, purpose: OrderPurpose) -> list[Order]:
    query = (
        select(Order)
        .where(Order.position_id == position.id, Order.purpose == purpose)
        .order_by(Order.id)
    )
    return list(session.scalars(query).all())


def events(position: Position) -> list[PositionEventType]:
    return [e.type for e in position.events]


# --- accept and reject ----------------------------------------------------------------------


def test_accepting_sends_the_opening_order_at_the_mid_credit(session: Session) -> None:
    broker = FakeBroker()
    opportunity = spread(session)

    position = accept(session, broker, opportunity.id, "click-0001")

    _, request = broker.last()
    assert request.credit and request.limit_price == 1.0 and request.quantity == 2
    assert [leg.intent for leg in request.legs] == ["sell_to_open", "buy_to_open"]
    assert position.status == PositionStatus.PENDING
    assert opportunity.status == OpportunityStatus.ACCEPTED
    order = orders(session, position, OrderPurpose.OPEN)[0]
    assert order.status == OrderStatus.SUBMITTED and order.broker_order_id is not None
    assert order.idempotency_key == request.client_order_id
    # Net natural and far quotes: 2.0 - 1.1 and 2.1 - 1.0.
    assert order.quote_bid == Decimal("0.9") and order.quote_ask == Decimal("1.1")


def test_a_double_click_sends_one_order(session: Session) -> None:
    broker = FakeBroker()
    opportunity = spread(session)

    first = accept(session, broker, opportunity.id, "click-0001")
    second = accept(session, broker, opportunity.id, "click-0001")

    assert first.id == second.id and len(broker.requests) == 1
    with pytest.raises(DecisionError):
        accept(session, broker, opportunity.id, "click-0002")


def test_acceptance_rechecks_the_portfolio_limits(session: Session) -> None:
    broker = FakeBroker()
    opportunity = spread(session)

    # On 1 000 of capital, 800 of max loss breaks the 10 % open loss and 5 % cluster limits.
    with pytest.raises(DecisionError, match="Entrée bloquée") as error:
        accept(session, broker, opportunity.id, "click-0001", 1_000)
    assert "perte maximale ouverte totale" in str(error.value)
    assert "cluster" in str(error.value) and "collatéral" in str(error.value)
    assert not broker.requests and opportunity.status == OpportunityStatus.PROPOSED


def test_rejecting_records_the_reason_and_sends_nothing(session: Session) -> None:
    opportunity = spread(session)

    decision = reject_opportunity(
        session, opportunity.id, "click-0001", RejectReason.NEWS, "Fed demain"
    )

    assert decision.reject_reason == RejectReason.NEWS
    assert opportunity.status == OpportunityStatus.REJECTED
    assert reject_opportunity(session, opportunity.id, "click-0001", RejectReason.NEWS) is decision


def test_api_accept_and_reject(session: Session) -> None:
    broker = FakeBroker()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_broker] = lambda: broker
    try:
        client = TestClient(app)
        first, second = spread(session), spread(session)
        healthy(session, broker, first.id)
        accepted = client.post(
            f"/opportunities/{first.id}/accept", json={"idempotency_key": "click-0001"}
        )
        rejected = client.post(
            f"/opportunities/{second.id}/reject",
            json={"idempotency_key": "click-0002", "reason": "no_conviction"},
        )
        missing = client.post(
            "/opportunities/999999/reject",
            json={"idempotency_key": "click-0003", "reason": "other"},
        )
    finally:
        app.dependency_overrides.clear()

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["position_status"] == "pending"
    assert accepted.json()["order_status"] == "submitted"
    assert rejected.status_code == 200 and rejected.json()["reason"] == "no_conviction"
    assert missing.status_code == 404


# --- order sync -----------------------------------------------------------------------------


def test_a_fill_opens_the_position_and_places_the_gtc_profit_target(session: Session) -> None:
    broker = FakeBroker()

    position = open_spread(session, broker)

    assert position.status == PositionStatus.OPEN
    assert position.entry_credit == Decimal("1.05")
    assert {leg.symbol: leg.avg_price for leg in position.legs} == {
        SHORT: Decimal("2.05"),
        LONG: Decimal("1"),
    }
    [target] = orders(session, position, OrderPurpose.TAKE_PROFIT)
    _, request = broker.last()
    assert target.status == OrderStatus.SUBMITTED and target.time_in_force == "gtc"
    assert request.limit_price == take_profit_price(1.05, PARAMS) and not request.credit
    assert [leg.intent for leg in request.legs] == ["buy_to_close", "sell_to_close"]
    assert events(position) == [PositionEventType.OPENED, PositionEventType.TAKE_PROFIT_PLACED]


def test_the_profit_target_fill_closes_the_position(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    order_id, _ = broker.last()

    broker.fill(order_id, {SHORT: 0.60, LONG: 0.08})
    sync_orders(session, broker)

    assert position.status == PositionStatus.CLOSED
    assert position.exit_reason == ExitReason.PROFIT_TARGET
    assert position.exit_debit == Decimal("0.52")
    assert position.realized_pnl == Decimal("106.00")


def test_an_unfilled_opening_order_cancels_the_position(session: Session) -> None:
    broker = FakeBroker()
    position = accept(session, broker, spread(session).id, "click-0001")
    order_id, _ = broker.last()

    broker.set_status(order_id, "expired")
    sync_orders(session, broker)

    assert position.status == PositionStatus.CANCELED
    assert not orders(session, position, OrderPurpose.TAKE_PROFIT)


def test_a_broker_rejection_cancels_the_position(session: Session) -> None:
    broker = FakeBroker()
    broker.fail_next = BrokerError("insufficient options buying power", 403)

    position = accept(session, broker, spread(session).id, "click-0001")

    assert position.status == PositionStatus.CANCELED
    assert events(position) == [PositionEventType.ORDER_REJECTED]


def test_a_network_failure_is_retried_by_the_next_sync(session: Session) -> None:
    broker = FakeBroker()
    broker.fail_next = BrokerError("timeout")

    position = accept(session, broker, spread(session).id, "click-0001")
    [order] = orders(session, position, OrderPurpose.OPEN)
    assert order.status == OrderStatus.NEW and not broker.requests

    sync_orders(session, broker)

    assert order.status == OrderStatus.SUBMITTED
    assert broker.last()[1].client_order_id == order.idempotency_key


# --- automatic exits ------------------------------------------------------------------------


def test_the_stop_is_confirmed_then_buys_back_with_a_progressive_limit(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    target_id, _ = broker.last()
    # Mid cost to close 3.1 - 0.95 = 2.15 and natural 3.2 - 0.9 = 2.30: the expected buy-back
    # 2.225 is above the stop at 2 x the 1.05 filled credit.
    broker.quotes = {SHORT: Quote(3.0, 3.2), LONG: Quote(0.9, 1.0)}

    check_exits(session, broker, TODAY + timedelta(days=5))
    assert not orders(session, position, OrderPurpose.STOP_LOSS)  # one mark is not enough
    assert broker.orders[target_id].status == "submitted"

    check_exits(session, broker, TODAY + timedelta(days=5))

    assert broker.orders[target_id].status == "canceled"
    [stop] = orders(session, position, OrderPurpose.STOP_LOSS)
    _, request = broker.last()
    # First step: a third of the way from the mid to the natural.
    assert request.limit_price == 2.2 and request.time_in_force == "day"
    assert stop.reprice_step == 1
    triggered = next(e for e in position.events if e.type == PositionEventType.STOP_TRIGGERED)
    assert triggered.payload["triggers"] == ["cost"] and triggered.payload["natural"] == 2.3
    assert position.exit_mid == Decimal("2.15") and position.exit_natural == Decimal("2.3")
    assert position.status == PositionStatus.OPEN

    check_exits(session, broker, TODAY + timedelta(days=5))
    assert len(orders(session, position, OrderPurpose.STOP_LOSS)) == 1  # still within its step

    # Unfilled after the re-pricing interval: canceled and sent one step closer.
    stop.submitted_at -= timedelta(minutes=PARAMS.exit_reprice_minutes)
    check_exits(session, broker, TODAY + timedelta(days=5))
    first, second = orders(session, position, OrderPurpose.STOP_LOSS)
    assert first.status == OrderStatus.CANCELED and first.replaced
    second_id, request = broker.last()
    assert request.limit_price == 2.25 and second.reprice_step == 2
    assert PositionEventType.ORDER_REPRICED in events(position)

    # Filled at the natural, not at the mid the stop was measured on: the stop does not
    # guarantee the price, the loss is taken from the fill.
    broker.fill(second_id, {SHORT: 3.2, LONG: 0.9})
    sync_orders(session, broker)
    assert position.status == PositionStatus.CLOSED
    assert position.exit_reason == ExitReason.STOP_LOSS
    assert position.exit_debit == Decimal("2.3")
    assert position.realized_pnl == Decimal("-250.00")


def test_a_delta_signal_stops_before_the_cost_does(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    # Mid 1.55 - 0.75 = 0.80, far from the 2.10 stop, but the short put is at -0.55 delta.
    broker.quotes = {SHORT: Quote(1.5, 1.6, delta=-0.55), LONG: Quote(0.7, 0.8, delta=-0.45)}

    check_exits(session, broker, TODAY + timedelta(days=5))

    [stop] = orders(session, position, OrderPurpose.STOP_LOSS)
    triggered = next(e for e in position.events if e.type == PositionEventType.STOP_TRIGGERED)
    assert triggered.payload["triggers"] == ["delta"]
    session.flush()
    query = select(PositionMark).where(PositionMark.position_id == position.id)
    mark = session.scalars(query).one()
    assert mark.delta == Decimal("-0.55") and mark.natural == Decimal("0.9")


def test_the_underlying_through_the_short_strike_stops(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    broker.quotes = {SHORT: Quote(1.5, 1.6), LONG: Quote(0.7, 0.8)}
    broker.prices = {"SPY": 499.0}

    check_exits(session, broker, TODAY + timedelta(days=5))

    assert orders(session, position, OrderPurpose.STOP_LOSS)
    triggered = next(e for e in position.events if e.type == PositionEventType.STOP_TRIGGERED)
    assert triggered.payload["triggers"] == ["breach"]


def test_earnings_before_the_expiration_close_the_position(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    opportunity = session.get(Opportunity, position.opportunity_id)
    opportunity.next_earnings = TODAY + timedelta(days=6)
    broker.quotes = {SHORT: Quote(1.5, 1.6), LONG: Quote(0.7, 0.8)}

    check_exits(session, broker, TODAY + timedelta(days=2))
    assert not orders(session, position, OrderPurpose.STOP_LOSS)

    check_exits(session, broker, TODAY + timedelta(days=4))
    assert orders(session, position, OrderPurpose.STOP_LOSS)


def test_the_entry_steps_toward_the_natural_credit(session: Session) -> None:
    broker = FakeBroker()
    position = accept(session, broker, spread(session).id, "click-0001")
    [first] = orders(session, position, OrderPurpose.OPEN)
    assert first.reprice_step == 0 and first.limit_price == Decimal("1.00")
    assert position.entry_mid == Decimal("1") and position.entry_natural == Decimal("0.9")

    reprice_entries(session, broker)
    assert len(orders(session, position, OrderPurpose.OPEN)) == 1  # too early

    limits = []
    for _ in range(4):
        last = orders(session, position, OrderPurpose.OPEN)[-1]
        last.submitted_at -= timedelta(minutes=PARAMS.entry_reprice_minutes)
        reprice_entries(session, broker)
        limits.append(float(orders(session, position, OrderPurpose.OPEN)[-1].limit_price))
    # Mid 1.00 to natural 0.90 in three steps, then the last step stays.
    assert limits == [0.97, 0.93, 0.9, 0.9]
    sent = orders(session, position, OrderPurpose.OPEN)
    assert [o.status for o in sent[:-1]] == [OrderStatus.CANCELED] * 3
    assert all(o.replaced for o in sent[:-1]) and sent[-1].reprice_step == 3
    assert position.status == PositionStatus.PENDING  # canceled for a new step, not abandoned

    broker.fill(broker.last()[0], {SHORT: 2.0, LONG: 1.1})
    sync_orders(session, broker)
    assert position.status == PositionStatus.OPEN
    # Exits work from the credit actually filled: 0.90, not the 1.00 mid.
    assert position.entry_credit == Decimal("0.9")
    assert broker.last()[1].limit_price == take_profit_price(0.9, PARAMS)


def test_a_limit_set_by_hand_is_never_moved(session: Session) -> None:
    broker = FakeBroker()
    position = accept(session, broker, spread(session).id, "click-0001", limit_price=1.02)
    [order] = orders(session, position, OrderPurpose.OPEN)
    order.submitted_at -= timedelta(hours=1)

    reprice_entries(session, broker)

    assert order.reprice_step is None and len(orders(session, position, OrderPurpose.OPEN)) == 1


def test_time_exit_at_21_dte(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    broker.quotes = {SHORT: Quote(1.5, 1.6), LONG: Quote(0.7, 0.8)}

    check_exits(session, broker, EXPIRATION_DAY - timedelta(days=22))
    assert not orders(session, position, OrderPurpose.TIME_EXIT)

    check_exits(session, broker, EXPIRATION_DAY - timedelta(days=21))
    [time_exit] = orders(session, position, OrderPurpose.TIME_EXIT)
    [target] = orders(session, position, OrderPurpose.TAKE_PROFIT)
    assert target.status == OrderStatus.CANCELED
    # Mid 0.80, natural 1.6 - 0.7 = 0.90: first step at a third of the way, rounded up.
    assert time_exit.limit_price == Decimal("0.84")
    assert PositionEventType.TIME_EXIT_TRIGGERED in events(position)


def test_marks_are_stored_and_the_resting_target_is_left_alone(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    broker.quotes = {SHORT: Quote(0.50, 0.52), LONG: Quote(0.05, 0.07)}  # mid 0.45

    check_exits(session, broker, TODAY + timedelta(days=5))

    assert len(broker.requests) == 2  # the opening order and the target, nothing else
    session.flush()
    [mark] = session.scalars(select(PositionMark).where(PositionMark.position_id == position.id))
    assert mark.mark == Decimal("0.45") and mark.unrealized_pnl == Decimal("120.00")


# --- expiration and the wheel ---------------------------------------------------------------


def test_worthless_expiration_keeps_the_credit_once(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    broker.activities = [
        Activity("a1", EXPIRATION, SHORT, 2, EXPIRATION_DAY),
        Activity("a2", EXPIRATION, LONG, 2, EXPIRATION_DAY),
    ]

    sync_activities(session, broker, EXPIRATION_DAY + timedelta(days=1))
    sync_activities(session, broker, EXPIRATION_DAY + timedelta(days=1))

    assert position.status == PositionStatus.EXPIRED
    assert position.realized_pnl == Decimal("210.00")
    assert events(position).count(PositionEventType.EXPIRED) == 1


def test_the_wheel_from_put_assignment_to_shares_called_away(session: Session) -> None:
    broker = FakeBroker()
    put = make_opportunity(
        session,
        StrategyType.CASH_SECURED_PUT,
        "SOFI",
        [(SOFI_PUT, OptionType.PUT, Side.SELL, 15, 0.38, 0.42)],
        0.40,
        1,
        1500,
        stress_loss=410,
    )
    csp = accept(session, broker, put.id, "click-0001")
    broker.fill(broker.last()[0], {SOFI_PUT: 0.40})
    sync_orders(session, broker)

    broker.activities = [Activity("a1", ASSIGNMENT, SOFI_PUT, 1, TODAY + timedelta(days=10))]
    sync_activities(session, broker, TODAY + timedelta(days=11))
    session.flush()

    assert csp.status == PositionStatus.ASSIGNED and csp.realized_pnl == Decimal("0")
    lot = session.scalars(select(Position).where(Position.parent_position_id == csp.id)).one()
    [stock] = lot.legs
    assert stock.instrument_type == InstrumentType.STOCK and stock.quantity == 100
    assert stock.avg_price == Decimal("14.60") and lot.collateral == Decimal("1500")
    # Shares: stock to zero as the contractual max loss, a 30 % gap as the stress loss.
    assert lot.max_loss == Decimal("1460") and lot.stress_loss == Decimal("438.00")
    assert [(lot_.position_id, lot_.shares) for lot_ in share_lots(session)] == [(lot.id, 100)]

    call = make_opportunity(
        session,
        StrategyType.COVERED_CALL,
        "SOFI",
        [(SOFI_CALL, OptionType.CALL, Side.SELL, 16, 0.28, 0.32)],
        0.30,
        1,
        0,
    )
    cc = accept(session, broker, call.id, "click-0002")
    assert cc.parent_position_id == lot.id
    broker.fill(broker.last()[0], {SOFI_CALL: 0.30})
    sync_orders(session, broker)
    assert cc.status == PositionStatus.OPEN and share_lots(session) == []

    broker.activities.append(Activity("a2", ASSIGNMENT, SOFI_CALL, 1, TODAY + timedelta(days=30)))
    sync_activities(session, broker, TODAY + timedelta(days=31))
    sync_activities(session, broker, TODAY + timedelta(days=31))

    assert cc.status == PositionStatus.ASSIGNED and cc.exit_reason == ExitReason.CALLED_AWAY
    assert cc.realized_pnl == Decimal("30")
    assert lot.status == PositionStatus.CLOSED and lot.exit_reason == ExitReason.CALLED_AWAY
    assert lot.realized_pnl == Decimal("140")  # (16 - 14.60) x 100
    assert stock.quantity == 0 and lot.collateral == Decimal("0")


@pytest.mark.parametrize(
    ("strategy", "true_wheel", "stopped"),
    [
        (StrategyType.SHORT_PUT, [], True),  # Short Put Income: bought back
        (StrategyType.CASH_SECURED_PUT, ["SOFI"], False),  # True Wheel: may be assigned
    ],
)
def test_only_a_true_wheel_put_waits_for_assignment(
    session: Session, strategy: StrategyType, true_wheel: list, stopped: bool
) -> None:
    config = active_config(session)
    config.params = {**config.params, "true_wheel": true_wheel}
    session.flush()
    broker = FakeBroker()
    put = make_opportunity(
        session,
        strategy,
        "SOFI",
        [(SOFI_PUT, OptionType.PUT, Side.SELL, 15, 0.38, 0.42)],
        0.40,
        1,
        1500,
        stress_loss=410,
    )
    position = accept(session, broker, put.id, "click-0001")
    broker.fill(broker.last()[0], {SOFI_PUT: 0.40})
    sync_orders(session, broker)
    broker.quotes = {SOFI_PUT: Quote(1.50, 1.60)}  # four times the credit

    check_exits(session, broker, TODAY + timedelta(days=5))
    check_exits(session, broker, TODAY + timedelta(days=5))  # the stop needs two marks

    assert bool(orders(session, position, OrderPurpose.STOP_LOSS)) == stopped
    broker.activities = [Activity("a1", ASSIGNMENT, SOFI_PUT, 1, TODAY + timedelta(days=10))]
    if not stopped:
        sync_activities(session, broker, TODAY + timedelta(days=11))
        assert position.status == PositionStatus.ASSIGNED
        assert [lot.shares for lot in share_lots(session)] == [100]
