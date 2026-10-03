"""Exit rules for short premium positions: profit target, stop loss and time exit.

The profit target is meant to rest at the broker as a GTC order placed right after the fill;
the position monitor evaluates all three rules on each mark and is the fallback for the target.
"""

from dataclasses import dataclass
from datetime import date

from app.domain.params import StrategyParams

# Values match app.models.enums.ExitReason.
PROFIT_TARGET = "profit_target"
STOP_LOSS = "stop_loss"
TIME_EXIT = "time_exit"


@dataclass(frozen=True)
class ShortPremium:
    strategy: str  # put_credit_spread | cash_secured_put | covered_call
    credit: float  # net credit received per share at entry
    expiration: date


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    # Cost to close per share when the rule fired.
    mark: float


def take_profit_price(credit: float, params: StrategyParams) -> float:
    """Debit limit for the GTC buy-to-close order: keep `take_profit_pct` of the credit."""
    return round(credit * (1 - params.take_profit_pct), 2)


def stop_price(credit: float, params: StrategyParams) -> float:
    """Cost to close at which the position is bought back."""
    return round(credit * params.stop_loss_multiple, 2)


def evaluate_exit(
    position: ShortPremium, mark: float, today: date, params: StrategyParams
) -> ExitSignal | None:
    """Return the exit to execute now, if any, given the current cost to close (mid).

    Covered calls have no stop: the shares cover the call, and being called away is part of
    the wheel. The stop is checked first so a losing position at 21 DTE is tagged as a stop.
    """
    if position.strategy != "covered_call" and mark >= stop_price(position.credit, params):
        return ExitSignal(STOP_LOSS, mark)
    if mark <= take_profit_price(position.credit, params):
        return ExitSignal(PROFIT_TARGET, mark)
    if (position.expiration - today).days <= params.exit_dte:
        return ExitSignal(TIME_EXIT, mark)
    return None
