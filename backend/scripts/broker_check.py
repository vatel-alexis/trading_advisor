"""Check the Alpaca paper wiring without a database.

    python -m scripts.broker_check                # read-only: account, clock, quotes
    python -m scripts.broker_check --probe-orders # also send two unfillable orders, then cancel

The probe sends a far out-of-the-money SPY put credit spread asking a credit almost equal to
its width, and a single put asking 50 dollars: neither can fill. Both are canceled at once and
the script fails loudly if a cancel is not confirmed.
"""

import argparse
import json
import sys
import time
import uuid
from dataclasses import replace
from datetime import date, timedelta

from app.broker.alpaca import DATA_URL, AlpacaBroker
from app.config import get_settings
from app.domain.orders import close_order, cost_to_close, open_order


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-orders", action="store_true")
    args = parser.parse_args()

    broker = AlpacaBroker.from_settings(get_settings())
    account = broker._call("GET", "/v2/account")
    print(
        "compte",
        {
            k: account[k]
            for k in ("status", "options_trading_level", "cash", "options_buying_power")
        },
    )
    print("marché ouvert :", broker.market_is_open())
    print("positions :", len(broker._call("GET", "/v2/positions")))
    print("activités options (30 j) :", broker.option_activities(date.today() - timedelta(days=30)))

    spot = broker._call("GET", "/v2/stocks/SPY/trades/latest?feed=iex", base=DATA_URL)["trade"]["p"]
    today = date.today()
    contracts = broker._call(
        "GET",
        "/v2/options/contracts?underlying_symbols=SPY&type=put&limit=500"
        f"&expiration_date_gte={today + timedelta(days=30)}"
        f"&expiration_date_lte={today + timedelta(days=50)}"
        f"&strike_price_lte={spot * 0.75:.0f}&strike_price_gte={spot * 0.65:.0f}",
    )["option_contracts"]
    expiration = min(c["expiration_date"] for c in contracts)
    strikes = {
        float(c["strike_price"]): c["symbol"]
        for c in contracts
        if c["expiration_date"] == expiration
    }
    short_strike = next(s for s in sorted(strikes, reverse=True) if s - 5 in strikes)
    legs = [(strikes[short_strike], "sell"), (strikes[short_strike - 5], "buy")]
    quotes = broker.option_quotes([symbol for symbol, _ in legs])
    print(f"SPY {spot} ; spread {legs} ; cotations {quotes}")
    print(
        "coût de rachat mid / naturel :",
        cost_to_close(legs, quotes),
        cost_to_close(legs, quotes, True),
    )

    examples = [
        open_order("exemple-ouverture", legs, 1, 0.42),
        close_order("exemple-tp", legs, 1, 0.21, "gtc"),
        open_order("exemple-csp", legs[:1], 1, 0.42),
    ]
    for request in examples:
        print(json.dumps(AlpacaBroker.order_payload(request)))

    if not args.probe_orders:
        return 0

    probes = [
        open_order(f"ta-probe-{uuid.uuid4().hex[:8]}", legs, 1, 4.90),
        open_order(f"ta-probe-{uuid.uuid4().hex[:8]}", legs[:1], 1, 50.0),
    ]
    failed = False
    for request in probes:
        for time_in_force in ("gtc", "day"):
            probe = replace(
                request,
                time_in_force=time_in_force,
                client_order_id=f"{request.client_order_id}-{time_in_force}",
            )
            kind = "spread" if len(probe.legs) > 1 else "simple"
            try:
                order = broker.submit_order(probe)
            except Exception as exc:  # noqa: BLE001 - report every refusal
                print(f"{kind} {time_in_force} : refusé ({exc})")
                continue
            print(f"{kind} {time_in_force} : accepté, statut {order.status}")
            broker.cancel_order(order.id)
            for _ in range(5):
                order = broker.get_order(order.id)
                if order.status == "canceled":
                    break
                time.sleep(1)
            print(f"  annulé : {order.status}")
            failed |= order.status != "canceled"
            assert broker.find_order(probe.client_order_id) is not None
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
