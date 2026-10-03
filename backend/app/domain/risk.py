"""Position sizing and portfolio limits."""

import math
from dataclasses import dataclass

from app.domain.params import StrategyParams


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


def size_position(collateral_per_unit: float, account: AccountState, params: StrategyParams) -> int:
    """Largest quantity whose collateral fits both the per-trade and the portfolio limits."""
    if collateral_per_unit <= 0:
        return 0
    budget = min(account.capital * params.max_trade_pct, account.remaining_capacity(params))
    return math.floor(budget / collateral_per_unit + 1e-9)
