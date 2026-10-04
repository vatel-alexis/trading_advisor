import json
from datetime import date

import pytest

from app.broker import ASSIGNMENT, EXPIRATION, BrokerError
from app.broker.alpaca import AlpacaBroker
from app.domain.orders import (
    Quote,
    close_order,
    cost_to_close,
    net_credit,
    open_order,
    price_up,
    realized_pnl,
)

SPREAD = [("SPY261106P00575000", "sell"), ("SPY261106P00570000", "buy")]


# --- domain ---------------------------------------------------------------------------------


def test_open_order_sells_to_open_and_close_order_reverses_each_leg() -> None:
    opening = open_order("k1", SPREAD, 2, 0.4249)
    closing = close_order("k2", SPREAD, 2, 0.21, "gtc")

    assert [(leg.side, leg.intent) for leg in opening.legs] == [
        ("sell", "sell_to_open"),
        ("buy", "buy_to_open"),
    ]
    assert opening.credit and opening.limit_price == 0.42 and opening.time_in_force == "day"
    assert [(leg.side, leg.intent) for leg in closing.legs] == [
        ("buy", "buy_to_close"),
        ("sell", "sell_to_close"),
    ]
    assert not closing.credit and closing.time_in_force == "gtc"


def test_cost_to_close_at_mid_and_natural() -> None:
    quotes = {SPREAD[0][0]: Quote(0.25, 0.27), SPREAD[1][0]: Quote(0.20, 0.22)}

    assert cost_to_close(SPREAD, quotes) == pytest.approx(0.05)
    assert cost_to_close(SPREAD, quotes, natural=True) == pytest.approx(0.07)
    assert cost_to_close(SPREAD, {SPREAD[0][0]: Quote(0.25, 0.27)}) is None


def test_fill_arithmetic() -> None:
    assert net_credit([("sell", 2.05), ("buy", 1.0)]) == pytest.approx(1.05)
    assert realized_pnl(1.05, 0.52, 2) == 106.0
    assert price_up(2.301) == 2.31 and price_up(2.30) == 2.3 and price_up(0) == 0.01


# --- Alpaca mapping -------------------------------------------------------------------------


class Recorder:
    def __init__(self, *responses: tuple[int, object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method: str, url: str, body: object) -> tuple[int, bytes]:
        self.calls.append((method, url, body))
        status, data = self.responses.pop(0)
        return status, json.dumps(data).encode()


def test_paper_endpoint_only() -> None:
    with pytest.raises(ValueError):
        AlpacaBroker("k", "s", base_url="https://api.alpaca.markets")


def test_spread_payload_sends_a_negative_limit_for_a_credit() -> None:
    payload = AlpacaBroker.order_payload(open_order("k1", SPREAD, 2, 0.42))
    closing = AlpacaBroker.order_payload(close_order("k2", SPREAD, 2, 0.21, "gtc"))

    assert payload["order_class"] == "mleg" and payload["limit_price"] == "-0.42"
    assert payload["qty"] == "2" and payload["client_order_id"] == "k1"
    assert payload["legs"][0] == {
        "symbol": SPREAD[0][0],
        "ratio_qty": "1",
        "side": "sell",
        "position_intent": "sell_to_open",
    }
    assert closing["limit_price"] == "0.21" and closing["time_in_force"] == "gtc"


def test_single_leg_payload_is_a_simple_order() -> None:
    payload = AlpacaBroker.order_payload(open_order("k1", SPREAD[:1], 1, 0.42))

    assert "order_class" not in payload and "legs" not in payload
    assert payload["symbol"] == SPREAD[0][0] and payload["side"] == "sell"
    assert payload["limit_price"] == "0.42" and payload["position_intent"] == "sell_to_open"


def test_parse_a_filled_multi_leg_order() -> None:
    order = AlpacaBroker.parse_order(
        {
            "id": "o1",
            "client_order_id": "k1",
            "status": "filled",
            "filled_qty": "2",
            "filled_at": "2026-10-05T14:31:02.5Z",
            "legs": [
                {
                    "symbol": SPREAD[0][0],
                    "side": "sell",
                    "filled_qty": "2",
                    "filled_avg_price": "2.05",
                },
                {"symbol": SPREAD[1][0], "side": "buy", "filled_qty": "2", "filled_avg_price": "1"},
            ],
        }
    )

    assert order.status == "filled" and order.filled_quantity == 2
    assert net_credit([(f.side, f.price) for f in order.fills]) == pytest.approx(1.05)
    assert order.filled_at is not None and order.filled_at.tzinfo is not None


def test_pending_statuses_map_to_submitted() -> None:
    for status in ("new", "accepted", "pending_new", "pending_cancel", "done_for_day"):
        assert AlpacaBroker.parse_order({"id": "o", "status": status}).status == "submitted"


def test_errors_carry_the_status_and_only_server_side_ones_are_transient() -> None:
    broker = AlpacaBroker("k", "s", transport=Recorder((422, {"message": "insufficient"})))

    with pytest.raises(BrokerError) as exc:
        broker.cancel_order("o1")
    assert exc.value.status == 422 and not exc.value.transient
    assert BrokerError("timeout").transient and BrokerError("x", 503).transient


def test_find_order_returns_none_when_unknown() -> None:
    broker = AlpacaBroker("k", "s", transport=Recorder((404, {"message": "not found"})))

    assert broker.find_order("ta-open-1-abc") is None


def test_quotes_and_activities_are_parsed() -> None:
    recorder = Recorder(
        (200, {"snapshots": {SPREAD[0][0]: {"latestQuote": {"bp": 0.25, "ap": 0.27}}}}),
        (
            200,
            [
                {"id": "a1", "activity_type": "OPASN", "symbol": SPREAD[0][0], "qty": "2",
                 "date": "2026-10-06"},
                {"id": "a2", "activity_type": "OPEXP", "symbol": SPREAD[1][0], "qty": "-2",
                 "date": "2026-11-06"},
            ],
        ),
    )  # fmt: skip
    broker = AlpacaBroker("k", "s", transport=recorder)

    quotes = broker.option_quotes([s for s, _ in SPREAD])
    activities = broker.option_activities(date(2026, 10, 1))

    assert quotes == {SPREAD[0][0]: Quote(0.25, 0.27)}
    assert "feed=indicative" in recorder.calls[0][1]
    assert recorder.calls[0][1].startswith("https://data.alpaca.markets/v1beta1/options/snapshots")
    assert [(a.kind, a.quantity) for a in activities] == [(ASSIGNMENT, 2), (EXPIRATION, 2)]
    assert "after=2026-10-01" in recorder.calls[1][1]
