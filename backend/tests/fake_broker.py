"""In-memory broker for service tests: orders rest until the test fills or expires them."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

from app.broker import Activity, BrokerError, BrokerOrder, LegFill
from app.domain.orders import OrderRequest, Quote


class FakeBroker:
    def __init__(self) -> None:
        self.requests: dict[str, OrderRequest] = {}
        self.orders: dict[str, BrokerOrder] = {}
        self.quotes: dict[str, Quote] = {}
        self.activities: list[Activity] = []
        self.is_open = True
        self.fail_next: BrokerError | None = None
        # Every read fails as if Alpaca were unreachable.
        self.down = False

    # --- test controls --------------------------------------------------------------------

    def last(self) -> tuple[str, OrderRequest]:
        order_id = list(self.requests)[-1]
        return order_id, self.requests[order_id]

    def fill(self, order_id: str, prices: dict[str, float], quantity: int | None = None) -> None:
        request = self.requests[order_id]
        qty = request.quantity if quantity is None else quantity
        fills = tuple(
            LegFill(leg.symbol, leg.side, qty, prices[leg.symbol]) for leg in request.legs
        )
        self.orders[order_id] = replace(
            self.orders[order_id],
            status="filled",
            filled_quantity=qty,
            fills=fills,
            filled_at=datetime.now(UTC),
        )

    def set_status(self, order_id: str, status: str) -> None:
        self.orders[order_id] = replace(self.orders[order_id], status=status)

    def live(self) -> list[OrderRequest]:
        return [self.requests[i] for i, o in self.orders.items() if o.status == "submitted"]

    # --- Broker protocol ------------------------------------------------------------------

    def submit_order(self, request: OrderRequest) -> BrokerOrder:
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        order_id = f"b{len(self.orders) + 1}"
        self.requests[order_id] = request
        self.orders[order_id] = BrokerOrder(order_id, request.client_order_id, "submitted")
        return self.orders[order_id]

    def get_order(self, order_id: str) -> BrokerOrder:
        return self.orders[order_id]

    def find_order(self, client_order_id: str) -> BrokerOrder | None:
        return next((o for o in self.orders.values() if o.client_order_id == client_order_id), None)

    def cancel_order(self, order_id: str) -> None:
        if self.orders[order_id].status != "submitted":
            raise BrokerError("order is not cancelable", 422)
        self.set_status(order_id, "canceled")

    def option_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        if self.down:
            raise BrokerError("Alpaca injoignable")
        return {s: self.quotes[s] for s in symbols if s in self.quotes}

    def option_activities(self, since: date) -> list[Activity]:
        return [a for a in self.activities if a.day >= since]

    def market_is_open(self) -> bool:
        if self.down:
            raise BrokerError("Alpaca injoignable")
        return self.is_open
