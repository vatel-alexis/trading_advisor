"""Strategy and risk parameters. Stored as JSON in strategy_configs, one version per change."""

from dataclasses import asdict, dataclass, fields
from typing import Any

ETFS = ("SPY", "QQQ", "IWM")
LARGE_CAPS = (
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "JPM", "BAC", "XOM",
    "KO", "PFE", "T", "INTC",
)  # fmt: skip
# Cash-secured puts need strike <= 20: 10 % of a 20k account is 2 000 of collateral.
WHEEL = (
    "F", "SOFI", "AAL", "RIVN", "NCLH", "VALE", "ITUB", "HBAN", "KEY", "PCG",
    "CLF", "AGNC", "LYFT", "SNAP", "NIO", "RIG",
)  # fmt: skip


@dataclass(frozen=True)
class StrategyParams:
    # Universe: put credit spreads on ETFs and large caps, the wheel on cheap stocks.
    etfs: tuple[str, ...] = ETFS
    large_caps: tuple[str, ...] = LARGE_CAPS
    wheel: tuple[str, ...] = WHEEL
    wheel_max_strike: float = 20.0

    # Contract filters (thresholds validated after the Sprint 0 spike).
    dte_min: int = 25
    dte_max: int = 55
    delta_min: float = 0.15
    delta_max: float = 0.30
    min_open_interest: int = 100
    min_volume: int = 10
    max_spread_pct: float = 0.15
    min_iv_rank: float = 30.0
    min_aroc: float = 0.15

    # Spread construction: long leg this many dollars below the short leg, first width found.
    spread_widths: tuple[float, ...] = (5.0, 10.0, 2.5)
    min_credit: float = 0.25

    # Risk.
    max_trade_pct: float = 0.10
    max_engaged_pct: float = 0.50
    max_deals: int = 5
    max_per_sector: int = 2

    # Exits.
    take_profit_pct: float = 0.50
    stop_loss_multiple: float = 2.0
    exit_dte: int = 21

    # IV Rank: real IV history once enough days are stored, else a realized-volatility proxy.
    iv_rank_min_history: int = 120
    risk_free_rate: float = 0.04

    # Ranking score = weights . (PoP and AROC percentiles within the strategy, IVR/100).
    score_weight_pop: float = 0.4
    score_weight_aroc: float = 0.4
    score_weight_iv_rank: float = 0.2

    def group_of(self, symbol: str) -> str | None:
        if symbol in self.etfs:
            return "etf"
        if symbol in self.large_caps:
            return "large_cap"
        if symbol in self.wheel:
            return "wheel"
        return None

    @property
    def universe(self) -> tuple[str, ...]:
        return self.etfs + self.large_caps + self.wheel

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyParams":
        """Build from stored JSON; unknown keys are ignored, missing keys take the defaults."""
        known = {f.name for f in fields(cls)}
        values = {k: tuple(v) if isinstance(v, list) else v for k, v in data.items() if k in known}
        return cls(**values)
