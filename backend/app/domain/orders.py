"""Broker-neutral orders for opening and closing short premium positions, and fill arithmetic.

Prices are per share and positive; `credit` says which way the money flows. Each leg keeps the
side of the position (`sell` for the short leg); closing orders reverse it.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

BUY = "buy"
SELL = "sell"


@dataclass(frozen=True)
class OrderLeg:
    symbol: str
    side: str  # buy | sell, the side of this order
    intent: str  # sell_to_open | buy_to_open | buy_to_close | sell_to_close


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    legs: tuple[OrderLeg, ...]
    quantity: int
    limit_price: float
    credit: bool
    time_in_force: str = "day"


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


def price_up(value: float) -> float:
    """Round a debit up to the cent, so a marketable limit stays marketable."""
    return max(0.01, math.ceil(round(value * 100, 6)) / 100)


def price(value: float) -> float:
    return max(0.01, round(value, 2))


def open_order(
    client_order_id: str, legs: Sequence[tuple[str, str]], quantity: int, credit: float
) -> OrderRequest:
    """Sell the premium: `legs` are (symbol, position side), the limit is the net credit."""
    order_legs = tuple(OrderLeg(symbol, side, f"{side}_to_open") for symbol, side in legs)
    return OrderRequest(client_order_id, order_legs, quantity, price(credit), credit=True)


def close_order(
    client_order_id: str,
    legs: Sequence[tuple[str, str]],
    quantity: int,
    debit: float,
    time_in_force: str = "day",
) -> OrderRequest:
    """Buy the position back for at most `debit` per share."""
    order_legs = tuple(
        OrderLeg(symbol, BUY if side == SELL else SELL, f"{BUY if side == SELL else SELL}_to_close")
        for symbol, side in legs
    )
    return OrderRequest(
        client_order_id,
        order_legs,
        quantity,
        price(debit),
        credit=False,
        time_in_force=time_in_force,
    )


def cost_to_close(
    legs: Sequence[tuple[str, str]], quotes: Mapping[str, Quote], natural: bool = False
) -> float | None:
    """Per-share cost to buy the position back, at mid or at the natural (worst) price.

    None when a leg has no usable quote: no exit decision is taken on a partial picture.
    """
    total = 0.0
    for symbol, side in legs:
        quote = quotes.get(symbol)
        if quote is None or quote.ask <= 0:
            return None
        if side == SELL:
            total += quote.ask if natural else quote.mid
        else:
            total -= quote.bid if natural else quote.mid
    return max(total, 0.0)


def net_credit(fills: Sequence[tuple[str, float]]) -> float:
    """Net per-share credit of an order from its legs' (side, average price)."""
    return sum(p if side == SELL else -p for side, p in fills)


def realized_pnl(credit: float, debit: float, quantity: int) -> float:
    return round((credit - debit) * 100 * quantity, 2)
