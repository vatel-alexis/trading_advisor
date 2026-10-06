"""Yahoo Finance market data (standard library only): prices, earnings, sector, option chains."""

import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime

from app.domain.market import MarketSnapshot, OptionQuote, put_call_ratio

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class YahooClient:  # pragma: no cover - network
    """Minimal client: cookie + crumb, then the chart/options/quoteSummary APIs."""

    def __init__(self) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.opener.addheaders = [("User-Agent", USER_AGENT)]
        try:
            self.opener.open("https://fc.yahoo.com", timeout=15)
        except urllib.error.HTTPError:
            pass  # 404 is expected; it still sets the A3 cookie
        self.crumb = self._get("https://query1.finance.yahoo.com/v1/test/getcrumb").decode()

    def _get(self, url: str) -> bytes:
        for attempt in range(4):
            try:
                with self.opener.open(url, timeout=20) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if (exc.code != 429 and exc.code < 500) or attempt == 3:
                    raise
                time.sleep(5 * 2**attempt)
        raise RuntimeError("unreachable")

    def json(self, path: str, **params) -> dict:
        params["crumb"] = self.crumb
        url = f"https://query2.finance.yahoo.com{path}?{urllib.parse.urlencode(params)}"
        return json.loads(self._get(url))


def _quotes(rows: list[dict], option_type: str, expiration: date) -> list[OptionQuote]:
    return [
        OptionQuote(
            symbol=row["contractSymbol"],
            option_type=option_type,  # type: ignore[arg-type]
            expiration=expiration,
            strike=float(row["strike"]),
            bid=float(row.get("bid") or 0),
            ask=float(row.get("ask") or 0),
            iv=float(row.get("impliedVolatility") or 0),
            open_interest=int(row.get("openInterest") or 0),
            volume=int(row.get("volume") or 0),
        )
        for row in rows
    ]


class YahooProvider:  # pragma: no cover - network
    """Builds a MarketSnapshot per underlying, fetching only expirations in the DTE window."""

    def __init__(self, dte_min: int, dte_max: int) -> None:
        self.client = YahooClient()
        self.dte_min = dte_min
        self.dte_max = dte_max

    def snapshot(self, symbol: str, today: date) -> MarketSnapshot:
        chart = self.client.json(f"/v8/finance/chart/{symbol}", range="1y", interval="1d")
        quote = chart["chart"]["result"][0]["indicators"]["quote"][0]
        closes = [float(c) for c in quote["close"] if c]
        next_earnings, sector = None, None
        try:
            summary = self.client.json(
                f"/v10/finance/quoteSummary/{symbol}", modules="calendarEvents,assetProfile"
            )["quoteSummary"]["result"][0]
            dates = summary.get("calendarEvents", {}).get("earnings", {}).get("earningsDate")
            if dates:
                next_earnings = datetime.fromtimestamp(dates[0]["raw"], UTC).date()
            sector = summary.get("assetProfile", {}).get("sector")
        except Exception:
            pass  # ETFs have no calendar; a stock without one is rejected by the screener
        chain = self.client.json(f"/v7/finance/options/{symbol}")["optionChain"]["result"][0]
        spot = float(chain["quote"].get("regularMarketPrice") or closes[-1])
        options: list[OptionQuote] = []
        # The first answer already holds the nearest expiration, where most volume trades: it
        # joins the screened expirations for the put/call ratio, at no extra request.
        flow: list[OptionQuote] = []
        for block in chain.get("options", [])[:1]:
            first = datetime.fromtimestamp(block["expirationDate"], UTC).date()
            flow += _quotes(block.get("puts", []), "put", first)
            flow += _quotes(block.get("calls", []), "call", first)
        for ts in chain["expirationDates"]:
            expiration = datetime.fromtimestamp(ts, UTC).date()
            if not self.dte_min <= (expiration - today).days <= self.dte_max:
                continue
            data = self.client.json(f"/v7/finance/options/{symbol}", date=ts)
            block = data["optionChain"]["result"][0]["options"][0]
            options += _quotes(block.get("puts", []), "put", expiration)
            options += _quotes(block.get("calls", []), "call", expiration)
        return MarketSnapshot(
            symbol, spot, closes, options, next_earnings, sector, put_call_ratio(flow + options)
        )
