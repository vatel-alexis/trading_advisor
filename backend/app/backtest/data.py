"""Daily history for the backtest: closes, splits, earnings dates, sectors and volatility indices.

Everything comes from Yahoo (standard library only) and is cached as one JSON file, so the
simulation and its sensitivity runs do not hit the network again.
"""

import json
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Implied volatility indices: the 30-day ATM IV of the ETF itself (VIX is SPX, a close match).
VOL_INDEX = {"SPY": "^VIX", "QQQ": "^VXN"}
EARNINGS_EVENT = "2"  # eventtype of a quarterly report in Yahoo's earnings calendar
QUARTER_DAYS = 91


@dataclass
class SymbolHistory:
    symbol: str
    dates: list[date]
    # Split-adjusted closes: returns and volatility are continuous across splits.
    closes: list[float]
    # Splits after each day: closes[i] * split_factors[i] is the price that traded on day i.
    split_factors: list[float]
    earnings: list[date] = field(default_factory=list)
    sector: str | None = None
    # Same adjustment as the closes; the exits look at the day's range, not only the close.
    opens: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)
    highs: list[float] = field(default_factory=list)


@dataclass
class MarketHistory:
    symbols: dict[str, SymbolHistory]
    # Index symbol -> day -> level as a decimal (VIX 20 -> 0.20).
    vol_indices: dict[str, dict[date, float]]

    def to_json(self) -> dict:
        return {
            "symbols": {
                s: {
                    "dates": [d.isoformat() for d in h.dates],
                    "closes": h.closes,
                    "split_factors": h.split_factors,
                    "earnings": [d.isoformat() for d in h.earnings],
                    "sector": h.sector,
                    "opens": h.opens,
                    "lows": h.lows,
                    "highs": h.highs,
                }
                for s, h in self.symbols.items()
            },
            "vol_indices": {
                k: {d.isoformat(): v for d, v in levels.items()}
                for k, levels in self.vol_indices.items()
            },
        }

    @classmethod
    def from_json(cls, data: dict) -> "MarketHistory":
        symbols = {
            s: SymbolHistory(
                s,
                [date.fromisoformat(d) for d in h["dates"]],
                h["closes"],
                h["split_factors"],
                [date.fromisoformat(d) for d in h["earnings"]],
                h["sector"],
                h.get("opens", []),
                h.get("lows", []),
                h.get("highs", []),
            )
            for s, h in data["symbols"].items()
        }
        vols = {
            k: {date.fromisoformat(d): v for d, v in levels.items()}
            for k, levels in data["vol_indices"].items()
        }
        return cls(symbols, vols)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json()))

    @classmethod
    def load(cls, path: Path) -> "MarketHistory":
        return cls.from_json(json.loads(path.read_text()))


def split_factors(days: Sequence[date], splits: Iterable[tuple[date, float]]) -> list[float]:
    """Product of the split ratios that happen after each day."""
    splits = sorted(splits)
    return [_product(ratio for split_day, ratio in splits if split_day > day) for day in days]


def _product(values: Iterable[float]) -> float:
    out = 1.0
    for v in values:
        out *= v
    return out


def fill_earnings(known: Iterable[date], until: date) -> list[date]:
    """Known report dates plus quarterly estimates for gaps in Yahoo's calendar.

    Yahoo's history stops some months back; missing reports are spread evenly between two
    known dates, and repeated every quarter after the last one.
    """
    dates = sorted(set(known))
    if not dates:
        return []
    out = [dates[0]]
    for prev, nxt in zip(dates, dates[1:], strict=False):
        gap = (nxt - prev).days
        steps = round(gap / QUARTER_DAYS)
        for k in range(1, steps):
            out.append(prev + timedelta(days=round(gap * k / steps)))
        out.append(nxt)
    while out[-1] + timedelta(days=QUARTER_DAYS) <= until:
        out.append(out[-1] + timedelta(days=QUARTER_DAYS))
    return out


class HistoryFetcher:  # pragma: no cover - network
    def __init__(self, start: date, end: date) -> None:
        from app.marketdata.yahoo import YahooClient

        self.client = YahooClient()
        self.start = start
        self.end = end

    def _chart(self, symbol: str) -> dict:
        def ts(d: date) -> int:
            return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())

        data = self.client.json(
            f"/v8/finance/chart/{symbol}",
            period1=ts(self.start),
            period2=ts(self.end + timedelta(days=1)),
            interval="1d",
            events="split",
        )
        return data["chart"]["result"][0]

    @staticmethod
    def _day(ts: int) -> date:
        # Daily bars are stamped at the US open; UTC keeps the exchange date.
        return datetime.fromtimestamp(ts, UTC).date()

    def bars(self, symbol: str) -> tuple[list[tuple], list[tuple[date, float]]]:
        """(day, open, low, high, close) rows with a close, and (day, ratio) splits."""
        result = self._chart(symbol)
        q = result["indicators"]["quote"][0]
        rows = []
        for t, o, lo, hi, c in zip(
            result.get("timestamp", []), q["open"], q["low"], q["high"], q["close"], strict=True
        ):
            if c:
                c = float(c)
                rows.append((self._day(t), float(o or c), float(lo or c), float(hi or c), c))
        splits = [
            (self._day(int(s["date"])), s["numerator"] / s["denominator"])
            for s in result.get("events", {}).get("splits", {}).values()
        ]
        return rows, splits

    def earnings(self, symbol: str) -> list[date]:
        body = {
            "size": 100,
            "offset": 0,
            "sortField": "startdatetime",
            "sortType": "DESC",
            "entityIdType": "earnings",
            "includeFields": ["ticker", "startdatetime", "eventtype"],
            "query": {
                "operator": "and",
                "operands": [{"operator": "eq", "operands": ["ticker", symbol]}],
            },
        }
        req = urllib.request.Request(
            "https://query1.finance.yahoo.com/v1/finance/visualization"
            f"?crumb={self.client.crumb}&lang=en-US&region=US",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.client.opener.open(req, timeout=20) as resp:
            out = json.loads(resp.read())
        rows = out["finance"]["result"][0]["documents"][0]["rows"]
        known = {date.fromisoformat(r[1][:10]) for r in rows if str(r[2]) == EARNINGS_EVENT}
        cal = self.client.json(f"/v10/finance/quoteSummary/{symbol}", modules="calendarEvents")
        earnings = cal["quoteSummary"]["result"][0].get("calendarEvents", {}).get("earnings", {})
        for key in ("earningsDate", "earningsCallDate"):
            for item in earnings.get(key, []):
                known.add(datetime.fromtimestamp(item["raw"], UTC).date())
        return fill_earnings(known, self.end + timedelta(days=120))

    def sector(self, symbol: str) -> str | None:
        data = self.client.json(f"/v10/finance/quoteSummary/{symbol}", modules="assetProfile")
        return data["quoteSummary"]["result"][0].get("assetProfile", {}).get("sector")

    def fetch(self, symbols: Sequence[str], etfs: Sequence[str]) -> MarketHistory:
        out: dict[str, SymbolHistory] = {}
        for symbol in symbols:
            rows, splits = self.bars(symbol)
            days = [r[0] for r in rows]
            history = SymbolHistory(
                symbol,
                days,
                [r[4] for r in rows],
                split_factors(days, splits),
                opens=[r[1] for r in rows],
                lows=[r[2] for r in rows],
                highs=[r[3] for r in rows],
            )
            if symbol not in etfs:
                history.earnings = self.earnings(symbol)
                history.sector = self.sector(symbol)
            out[symbol] = history
            print(f"{symbol}: {len(days)} jours, {len(history.earnings)} résultats")
        vols = {}
        for index in VOL_INDEX.values():
            rows, _ = self.bars(index)
            vols[index] = {r[0]: r[4] / 100 for r in rows}
        return MarketHistory(out, vols)
