"""What the trading service needs from a broker. Alpaca paper is the only implementation."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from app.domain.orders import OrderRequest, Quote

# Activity kinds, from the broker's non-trade activities.
ASSIGNMENT = "assignment"
EXPIRATION = "expiration"
EXERCISE = "exercise"


class BrokerError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def transient(self) -> bool:
        """Network failure, rate limit or server error: the same call may succeed later."""
        return self.status is None or self.status == 429 or self.status >= 500


@dataclass(frozen=True)
class LegFill:
    symbol: str
    side: str
    quantity: int
    price: float


@dataclass(frozen=True)
class BrokerOrder:
    id: str
    client_order_id: str
    # One of app.models.enums.OrderStatus values.
    status: str
    # Strategy units filled (spreads for a multi-leg order, contracts otherwise).
    filled_quantity: int = 0
    fills: Sequence[LegFill] = field(default_factory=tuple)
    filled_at: datetime | None = None


@dataclass(frozen=True)
class Activity:
    id: str
    kind: str
    symbol: str
    quantity: int
    day: date


class Broker(Protocol):
    def submit_order(self, request: OrderRequest) -> BrokerOrder: ...

    def get_order(self, order_id: str) -> BrokerOrder: ...

    def find_order(self, client_order_id: str) -> BrokerOrder | None: ...

    def cancel_order(self, order_id: str) -> None: ...

    def option_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]: ...

    def option_activities(self, since: date) -> list[Activity]: ...

    def market_is_open(self) -> bool: ...
