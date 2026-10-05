"""Position sizing and portfolio limits.

Three amounts describe what a position can cost, and they are never mixed up:
- collateral: the cash the broker holds against it (spread max loss, CSP strike x 100);
- contractual max loss: the most the contract can lose (the stock going to 0 for a short put);
- stress loss: what it loses if the underlying gaps down by the stress move.

The risk of a trade, what its size is computed from, is its max loss for a spread and its
stress loss for a short put or shares. Open and pending positions always count in the
portfolio limits, not only the day's new deals.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from app.domain.params import StrategyParams

PUT_CREDIT_SPREAD = "put_credit_spread"
CASH_SECURED_PUT = "cash_secured_put"
COVERED_CALL = "covered_call"

INDEX_CLUSTER = "Indices US (SPY/QQQ/IWM)"

# Exact reasons a deal gets no contract, shown as is in the interface.
NO_TRADE_MESSAGES = {
    "risk_budget": "Aucun contrat ne tient dans le budget de risque d'un trade",
    "open_risk_limit": "La perte maximale ouverte totale serait dépassée",
    "cluster_limit": "La limite de risque du cluster corrélé serait dépassée",
    "sector_limit": "Le nombre maximal de positions du secteur est atteint",
    "capital_limit": "Le collatéral disponible ne suffit pas",
    "already_open": "Une position est déjà ouverte ou en attente sur ce sous-jacent",
    "cooldown": "Délai avant de revenir sur ce titre",
    "max_deals": "Nombre maximal de deals du jour atteint",
}


@dataclass(frozen=True)
class AccountState:
    # Simulated capital: starting capital plus realized P&L.
    capital: float
    # Collateral of open and pending positions (spread max loss, CSP strike x 100, share cost).
    engaged: float = 0.0

    def remaining_capacity(self, params: StrategyParams) -> float:
        return max(0.0, self.capital * params.max_engaged_pct - self.engaged)

    def with_engaged(self, amount: float) -> "AccountState":
        return AccountState(self.capital, self.engaged + amount)


def cluster_of(underlying: str, sector: str | None, params: StrategyParams) -> str:
    """Correlated group: the index ETFs together, then each sector (else the name itself)."""
    if underlying in params.etfs or sector == "ETF":
        return INDEX_CLUSTER
    return sector or underlying


def stress_move(underlying: str, sector: str | None, params: StrategyParams) -> float:
    if underlying in params.etfs or sector == "ETF":
        return params.stress_move_etf
    return params.stress_move_stock


def put_spread_stress_loss(
    spot: float, short_strike: float, long_strike: float, credit: float, move: float
) -> float:
    """Loss per spread (dollars) at expiration if the underlying falls by `move`."""
    shocked = spot * (1 - move)
    value = max(0.0, short_strike - shocked) - max(0.0, long_strike - shocked)
    return max(0.0, value - credit) * 100


def short_put_stress_loss(spot: float, strike: float, credit: float, move: float) -> float:
    """Loss per contract (dollars) at expiration if the underlying falls by `move`."""
    return max(0.0, strike - spot * (1 - move) - credit) * 100


def shares_stress_loss(price: float, shares: int, move: float) -> float:
    return price * shares * move


def trade_risk(strategy: str | None, max_loss: float, stress_loss: float) -> float:
    """What a position's size is computed from: max loss of a spread, else the stress loss."""
    if strategy == PUT_CREDIT_SPREAD:
        return max_loss
    if strategy == COVERED_CALL:
        return 0.0  # the shares carry the risk
    return stress_loss


@dataclass(frozen=True)
class Exposure:
    """An open or pending position (or a deal being added), as the limits see it. Totals."""

    underlying: str
    sector: str | None
    strategy: str | None  # None for shares held after an assignment
    max_loss: float
    stress_loss: float
    collateral: float
    expiration: date | None = None

    def risk(self) -> float:
        return trade_risk(self.strategy, self.max_loss, self.stress_loss)


@dataclass(frozen=True)
class Portfolio:
    capital: float
    exposures: tuple[Exposure, ...] = ()
    # Collateral held by something that is not an exposure (kept for callers that only know
    # the engaged total).
    other_engaged: float = 0.0

    @property
    def engaged(self) -> float:
        return self.other_engaged + sum(e.collateral for e in self.exposures)

    @property
    def open_max_loss(self) -> float:
        return sum(e.max_loss for e in self.exposures)

    @property
    def stress_loss(self) -> float:
        return sum(e.stress_loss for e in self.exposures)

    def cluster_risk(self, cluster: str, params: StrategyParams) -> float:
        return sum(
            e.risk()
            for e in self.exposures
            if cluster_of(e.underlying, e.sector, params) == cluster
        )

    def sector_count(self, sector: str | None) -> int:
        """Positions in a sector; covered calls ride on shares already counted."""
        return sum(1 for e in self.exposures if e.sector == sector and e.strategy != COVERED_CALL)

    @property
    def underlyings(self) -> set[str]:
        return {e.underlying for e in self.exposures}

    def with_exposure(self, exposure: Exposure) -> "Portfolio":
        return replace(self, exposures=(*self.exposures, exposure))

    def clusters(self, params: StrategyParams) -> dict[str, float]:
        out: dict[str, float] = {}
        for e in self.exposures:
            key = cluster_of(e.underlying, e.sector, params)
            out[key] = out.get(key, 0.0) + e.risk()
        return out


@dataclass
class Sizing:
    """Why a deal gets its number of contracts: each cap, and the one that binds."""

    quantity: int
    risk_budget: float
    risk_per_contract: float
    caps: dict[str, int] = field(default_factory=dict)
    binding: str | None = None
    # Reason code from NO_TRADE_MESSAGES when quantity is 0.
    no_trade: str | None = None

    def to_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "risk_budget": round(self.risk_budget, 2),
            "risk_per_contract": round(self.risk_per_contract, 2),
            "caps": self.caps,
            "binding": self.binding,
            "no_trade": self.no_trade,
        }


def _floor(budget: float, per_unit: float) -> int:
    if per_unit <= 0:
        return 10**6
    return max(0, math.floor(budget / per_unit + 1e-9))


def size_deal(
    *,
    underlying: str,
    sector: str | None,
    strategy: str,
    max_loss: float,
    stress_loss: float,
    collateral: float,
    portfolio: Portfolio,
    params: StrategyParams,
    score: float | None = None,
) -> Sizing:
    """Contracts = floor(risk budget / risk of one contract), within every portfolio limit.

    Amounts are per contract (one spread, one put). Every cap is computed after adding the
    deal to the open and pending positions; the smallest wins and is reported as binding.
    """
    capital = portfolio.capital
    risk_unit = trade_risk(strategy, max_loss, stress_loss)
    budget = capital * params.trade_risk_pct(score)
    cluster = cluster_of(underlying, sector, params)
    caps = {
        "risk_budget": _floor(budget, risk_unit),
        "open_risk_limit": _floor(
            capital * params.max_open_risk_pct - portfolio.open_max_loss, max_loss
        ),
        "cluster_limit": _floor(
            capital * params.max_cluster_risk_pct - portfolio.cluster_risk(cluster, params),
            risk_unit,
        ),
        "capital_limit": min(
            _floor(capital * params.max_trade_pct, collateral),
            _floor(capital * params.max_engaged_pct - portfolio.engaged, collateral),
        ),
    }
    binding = min(caps, key=lambda k: caps[k])
    quantity = caps[binding]
    return Sizing(
        quantity=quantity,
        risk_budget=budget,
        risk_per_contract=risk_unit,
        caps=caps,
        binding=binding,
        no_trade=binding if quantity == 0 else None,
    )


def check_limits(exposure: Exposure, portfolio: Portfolio, params: StrategyParams) -> list[str]:
    """Limits a deal of a fixed size would break once added (codes of NO_TRADE_MESSAGES)."""
    capital = portfolio.capital
    after = portfolio.with_exposure(exposure)
    cluster = cluster_of(exposure.underlying, exposure.sector, params)
    out = []
    if exposure.strategy != COVERED_CALL:
        if exposure.underlying in portfolio.underlyings:
            out.append("already_open")
        if after.open_max_loss > capital * params.max_open_risk_pct + 0.01:
            out.append("open_risk_limit")
        if after.cluster_risk(cluster, params) > capital * params.max_cluster_risk_pct + 0.01:
            out.append("cluster_limit")
        if (
            params.use_sector_limit
            and exposure.sector
            and portfolio.sector_count(exposure.sector) >= params.max_per_sector
        ):
            out.append("sector_limit")
        if after.engaged > capital * params.max_engaged_pct + 0.01:
            out.append("capital_limit")
    return out


def expiration_concentration(exposures: Iterable[Exposure]) -> dict[str, float]:
    """Contractual max loss per expiration date."""
    out: dict[str, float] = {}
    for e in exposures:
        if e.expiration is not None:
            key = e.expiration.isoformat()
            out[key] = out.get(key, 0.0) + e.max_loss
    return dict(sorted(out.items()))


def portfolio_of(capital: float, exposures: Sequence[Exposure]) -> Portfolio:
    return Portfolio(capital, tuple(exposures))
