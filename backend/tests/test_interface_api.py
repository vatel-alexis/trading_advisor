from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session
from app.domain.exits import take_profit_price
from app.domain.orders import Quote
from app.main import app, get_broker
from app.models import PositionMark
from app.models.enums import (
    ExitReason,
    OpportunityStatus,
    OrderPurpose,
    OrderStatus,
    PositionStatus,
    RejectReason,
)
from app.services import views
from app.services.trading import (
    DecisionError,
    check_exits,
    close_position,
    reject_opportunity,
    sync_orders,
)
from tests.chains import TODAY
from tests.fake_broker import FakeBroker
from tests.test_trading_service import LONG, PARAMS, SHORT, open_spread, orders, spread

# --- manual buy-back ------------------------------------------------------------------------


def test_manual_close_cancels_the_target_and_buys_back_at_the_natural(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    target_id, _ = broker.last()
    broker.quotes = {SHORT: Quote(0.80, 0.90), LONG: Quote(0.20, 0.25)}

    order = close_position(session, broker, position.id, "close-0001")

    assert broker.orders[target_id].status == "canceled"
    _, request = broker.last()
    assert order.purpose == OrderPurpose.MANUAL_CLOSE and order.status == OrderStatus.SUBMITTED
    assert request.limit_price == 0.70 and request.time_in_force == "day"  # 0.90 - 0.20
    assert not request.credit
    # A double click returns the same order instead of sending a second one.
    assert close_position(session, broker, position.id, "close-0001").id == order.id
    assert len(orders(session, position, OrderPurpose.MANUAL_CLOSE)) == 1

    broker.fill(broker.last()[0], {SHORT: 0.9, LONG: 0.2})
    sync_orders(session, broker)
    assert position.status == PositionStatus.CLOSED
    assert position.exit_reason == ExitReason.MANUAL
    assert position.realized_pnl == Decimal("70.00")  # (1.05 - 0.70) x 100 x 2


def test_an_unfilled_manual_close_puts_the_profit_target_back(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    broker.quotes = {SHORT: Quote(0.80, 0.90), LONG: Quote(0.20, 0.25)}
    close_position(session, broker, position.id, "close-0001")

    broker.set_status(broker.last()[0], "expired")
    sync_orders(session, broker)

    assert position.status == PositionStatus.OPEN
    targets = sorted(orders(session, position, OrderPurpose.TAKE_PROFIT), key=lambda o: o.id)
    assert [t.status for t in targets] == [OrderStatus.CANCELED, OrderStatus.SUBMITTED]


def test_manual_close_is_refused_when_it_cannot_be_priced(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)

    with pytest.raises(DecisionError, match="cotation"):
        close_position(session, broker, position.id, "close-0001")
    broker.is_open = False
    with pytest.raises(DecisionError, match="Marché fermé"):
        close_position(session, broker, position.id, "close-0002")
    # The profit target still rests at the broker.
    assert orders(session, position, OrderPurpose.TAKE_PROFIT)[0].status == OrderStatus.SUBMITTED


# --- read models ----------------------------------------------------------------------------


def test_dashboard_and_positions_show_capital_and_the_last_mark(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    broker.quotes = {SHORT: Quote(1.0, 1.1), LONG: Quote(0.4, 0.5)}  # mid 0.60
    check_exits(session, broker, TODAY + timedelta(days=5))
    session.flush()

    board = views.dashboard(session, 20_000)
    assert board["engaged"] == 800 and board["available"] == 19_200
    assert board["engagement_capacity"] == 9_200  # 50 % of 20 000, minus 800
    assert board["open_positions"] == 1 and board["unrealized_pnl"] == 90.0

    [row] = views.positions(session, TODAY)["options"]
    assert row["id"] == position.id and row["mark"] == 0.6
    assert row["profit_pct"] == round(0.45 / 1.05, 4)
    assert row["take_profit_price"] == take_profit_price(1.05, PARAMS) and row["can_close"]
    assert [leg["strike"] for leg in row["legs"]] == [500, 495]
    assert session.query(PositionMark).count() >= 1


def test_opportunity_cards_carry_weight_ror_and_target(session: Session) -> None:
    opportunity = spread(session)
    opportunity.metrics = {"take_profit_price": 0.5, "stop_price": 2.0}
    session.flush()

    [card] = views.opportunities(session, 20_000, OpportunityStatus.PROPOSED)

    assert card["id"] == opportunity.id and card["short_strike"] == 500
    assert card["weight"] == 0.04  # 800 / 20 000
    assert card["credit_total"] == 200 and card["ror"] == 0.25
    assert card["take_profit_gain"] == 100.0


def test_history_lists_closed_and_rejected_deals_with_filters(session: Session) -> None:
    broker = FakeBroker()
    position = open_spread(session, broker)
    broker.fill(broker.last()[0], {SHORT: 0.60, LONG: 0.08})
    sync_orders(session, broker)
    rejected = spread(session)
    reject_opportunity(session, rejected.id, "click-0009", RejectReason.NEWS, "Fed")

    rows = views.history(session, views.HistoryFilter())
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["closed"]["id"] == f"p{position.id}" and by_kind["closed"]["pnl"] == 106.0
    assert by_kind["rejected"]["reason"] == "news" and by_kind["rejected"]["note"] == "Fed"

    only_rejected = views.history(session, views.HistoryFilter(kinds=("rejected",)))
    assert [r["kind"] for r in only_rejected] == ["rejected"]
    assert not views.history(session, views.HistoryFilter(underlying="QQQ"))
    future = views.history(session, views.HistoryFilter(since=TODAY + timedelta(days=3650)))
    assert not future


def test_api_read_routes_and_manual_close(session: Session) -> None:
    broker = FakeBroker()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_broker] = lambda: broker
    try:
        client = TestClient(app)
        position = open_spread(session, broker)
        spread(session)
        broker.quotes = {SHORT: Quote(0.80, 0.90), LONG: Quote(0.20, 0.25)}
        board = client.get("/dashboard")
        cards = client.get("/opportunities")
        held = client.get("/positions")
        closed = client.post(
            f"/positions/{position.id}/close", json={"idempotency_key": "close-0001"}
        )
        again = client.post(
            f"/positions/{position.id}/close", json={"idempotency_key": "close-0002"}
        )
        log = client.get("/history", params={"kind": ["closed", "rejected"], "strategy": "shares"})
        bad = client.get("/history", params={"kind": "nope"})
    finally:
        app.dependency_overrides.clear()

    assert board.status_code == 200 and board.json()["open_positions"] == 1
    assert cards.status_code == 200 and len(cards.json()) == 1
    assert held.status_code == 200 and held.json()["options"][0]["id"] == position.id
    assert closed.status_code == 200, closed.text
    assert closed.json()["order_status"] == "submitted" and closed.json()["limit_price"] == 0.7
    assert again.status_code == 409  # a buy-back is already working
    assert log.status_code == 200 and log.json()["rows"] == []
    assert bad.status_code == 422
