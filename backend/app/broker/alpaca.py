"""Alpaca paper trading client (standard library only): option orders, quotes and activities."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from app.broker import (
    ASSIGNMENT,
    EXERCISE,
    EXPIRATION,
    Activity,
    BrokerError,
    BrokerOrder,
    LegFill,
)
from app.config import PAPER_BROKER_URLS, Settings
from app.domain.orders import OrderRequest, Quote

DATA_URL = "https://data.alpaca.markets"

# (method, url, json body or None) -> (HTTP status, response body)
Transport = Callable[[str, str, dict[str, Any] | None], tuple[int, bytes]]

STATUS = {
    "filled": "filled",
    "partially_filled": "partially_filled",
    "canceled": "canceled",
    "expired": "expired",
    "rejected": "rejected",
}
ACTIVITY_KINDS = {"OPASN": ASSIGNMENT, "OPEXP": EXPIRATION, "OPEXC": EXERCISE}


class AlpacaBroker:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = PAPER_BROKER_URLS["alpaca"],
        transport: Transport | None = None,
    ) -> None:
        # Paper only: a live endpoint is refused even if passed explicitly.
        if base_url != PAPER_BROKER_URLS["alpaca"]:
            raise ValueError("Seul le compte paper Alpaca est autorisé.")
        self.base_url = base_url
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Content-Type": "application/json",
        }
        self.transport = transport or self._urllib

    @classmethod
    def from_settings(cls, settings: Settings) -> "AlpacaBroker":
        return cls(settings.broker_api_key, settings.broker_api_secret, settings.broker_base_url)

    def _urllib(
        self, method: str, url: str, body: dict[str, Any] | None
    ) -> tuple[int, bytes]:  # pragma: no cover - network
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BrokerError(f"Alpaca injoignable : {exc}") from exc

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None, base: str | None = None
    ) -> Any:
        url = (base or self.base_url) + path
        for attempt in range(3):
            status, raw = self.transport(method, url, body)
            # Retry rate limits on reads only: a retried POST could double an order.
            if status == 429 and method == "GET" and attempt < 2:
                time.sleep(2**attempt)
                continue
            break
        if status >= 400:
            try:
                message = json.loads(raw).get("message", raw.decode()[:200])
            except (ValueError, AttributeError):
                message = raw.decode(errors="replace")[:200]
            raise BrokerError(f"Alpaca {status} sur {method} {path} : {message}", status)
        return json.loads(raw) if raw else None

    # --- orders ---------------------------------------------------------------------------

    @staticmethod
    def order_payload(request: OrderRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "qty": str(request.quantity),
            "type": "limit",
            "time_in_force": request.time_in_force,
            "client_order_id": request.client_order_id,
        }
        if len(request.legs) == 1:
            leg = request.legs[0]
            payload |= {
                "symbol": leg.symbol,
                "side": leg.side,
                "position_intent": leg.intent,
                "limit_price": f"{request.limit_price:.2f}",
            }
            return payload
        # Multi-leg: a negative limit is a credit received, a positive one a debit paid.
        net = -request.limit_price if request.credit else request.limit_price
        payload |= {
            "order_class": "mleg",
            "limit_price": f"{net:.2f}",
            "legs": [
                {
                    "symbol": leg.symbol,
                    "ratio_qty": "1",
                    "side": leg.side,
                    "position_intent": leg.intent,
                }
                for leg in request.legs
            ],
        }
        return payload

    @staticmethod
    def parse_order(data: dict[str, Any]) -> BrokerOrder:
        legs = data.get("legs") or [data]
        fills = tuple(
            LegFill(
                leg["symbol"],
                leg["side"],
                int(float(leg.get("filled_qty") or 0)),
                float(leg["filled_avg_price"]),
            )
            for leg in legs
            if leg.get("filled_avg_price") and float(leg.get("filled_qty") or 0) > 0
        )
        filled_at = data.get("filled_at")
        return BrokerOrder(
            id=data["id"],
            client_order_id=data.get("client_order_id", ""),
            status=STATUS.get(data["status"], "submitted"),
            filled_quantity=int(float(data.get("filled_qty") or 0)),
            fills=fills,
            filled_at=datetime.fromisoformat(filled_at.replace("Z", "+00:00"))
            if filled_at
            else None,
        )

    def submit_order(self, request: OrderRequest) -> BrokerOrder:
        return self.parse_order(self._call("POST", "/v2/orders", self.order_payload(request)))

    def get_order(self, order_id: str) -> BrokerOrder:
        return self.parse_order(self._call("GET", f"/v2/orders/{order_id}"))

    def find_order(self, client_order_id: str) -> BrokerOrder | None:
        query = urllib.parse.urlencode({"client_order_id": client_order_id})
        try:
            return self.parse_order(self._call("GET", f"/v2/orders:by_client_order_id?{query}"))
        except BrokerError as exc:
            if exc.status == 404:
                return None
            raise

    def cancel_order(self, order_id: str) -> None:
        self._call("DELETE", f"/v2/orders/{order_id}")

    # --- market data and account ----------------------------------------------------------

    def option_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Latest bid/ask per option symbol from the indicative feed (free with paper)."""
        quotes: dict[str, Quote] = {}
        unique = list(dict.fromkeys(symbols))
        for i in range(0, len(unique), 100):
            params: dict[str, str] = {
                "symbols": ",".join(unique[i : i + 100]),
                "feed": "indicative",
            }
            while True:
                path = f"/v1beta1/options/snapshots?{urllib.parse.urlencode(params)}"
                data = self._call("GET", path, base=DATA_URL)
                for symbol, snap in (data.get("snapshots") or {}).items():
                    quote = snap.get("latestQuote") or {}
                    if quote.get("ap"):
                        delta = (snap.get("greeks") or {}).get("delta")
                        quotes[symbol] = Quote(
                            float(quote.get("bp") or 0),
                            float(quote["ap"]),
                            None if delta is None else float(delta),
                        )
                if not data.get("next_page_token"):
                    break
                params["page_token"] = data["next_page_token"]
        return quotes

    def stock_prices(self, symbols: Sequence[str]) -> dict[str, float]:
        """Last trade per stock or ETF from the IEX feed (free with paper)."""
        unique = list(dict.fromkeys(symbols))
        if not unique:
            return {}
        params = {"symbols": ",".join(unique), "feed": "iex"}
        data = self._call(
            "GET", f"/v2/stocks/trades/latest?{urllib.parse.urlencode(params)}", base=DATA_URL
        )
        return {
            symbol: float(trade["p"])
            for symbol, trade in (data.get("trades") or {}).items()
            if trade.get("p")
        }

    def option_activities(self, since: date) -> list[Activity]:
        """Assignments, expirations and exercises since `since`, oldest first.

        On paper these are only published at the start of the next day.
        """
        activities: list[Activity] = []
        params = {
            "activity_types": ",".join(ACTIVITY_KINDS),
            "after": since.isoformat(),
            "direction": "asc",
            "page_size": "100",
        }
        while True:
            rows = self._call("GET", f"/v2/account/activities?{urllib.parse.urlencode(params)}")
            for row in rows:
                activities.append(
                    Activity(
                        id=row["id"],
                        kind=ACTIVITY_KINDS[row["activity_type"]],
                        symbol=row["symbol"],
                        quantity=abs(int(float(row["qty"]))),
                        day=date.fromisoformat(row["date"][:10]),
                    )
                )
            if len(rows) < 100:
                return activities
            params["page_token"] = rows[-1]["id"]

    def market_is_open(self) -> bool:
        return bool(self._call("GET", "/v2/clock")["is_open"])
