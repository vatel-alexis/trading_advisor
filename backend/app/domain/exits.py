"""Exit rules for short premium positions: profit target, stop loss and time exit.

The profit target is meant to rest at the broker as a GTC order placed right after the fill;
the position monitor evaluates all three rules on each mark and is the fallback for the target.

With what the monitor sees (`MarketView`), the stop has several signals: the buy-back cost
(on the expected execution price, confirmed over consecutive marks, not on the mid alone),
the short leg's delta, the underlying through the short strike, a liquidation spread too wide
on a losing position, earnings before the expiration and the portfolio's drawdown limit.
A stop sends a limit order: it does not guarantee the price it fills at.
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
    strategy: str  # put_credit_spread | short_put | cash_secured_put | covered_call
    credit: float  # net credit received per share at entry
    expiration: date
    # True Wheel put: assignment is part of the plan, so neither the stop nor the time exit
    # buys it back (the profit target still does).
    assignment_accepted: bool = False


# Stop signals, in the order they are reported.
COST = "cost"
DELTA = "delta"
BREACH = "breach"
LIQUIDITY = "liquidity"
EVENT = "event"
PORTFOLIO = "portfolio"
TRIGGER_LABELS = {
    COST: "coût de rachat au-dessus du seuil",
    DELTA: "delta de la jambe vendue trop élevé",
    BREACH: "sous-jacent sous le strike vendu",
    LIQUIDITY: "écart de liquidation trop large",
    EVENT: "résultats avant l'échéance",
    PORTFOLIO: "drawdown max du portefeuille atteint",
}


@dataclass(frozen=True)
class MarketView:
    """What the monitor knows about a position on this pass, beyond the mid cost to close."""

    natural: float | None = None  # cost to close at the natural price
    # Consecutive marks, this one included, whose expected buy-back reached the stop.
    confirmations: int = 1
    short_delta: float | None = None  # |delta| of the short leg
    spot: float | None = None
    short_strike: float | None = None
    next_event: date | None = None
    portfolio_breach: bool = False


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    # Cost to close per share when the rule fired.
    mark: float
    # Stop signals that fired, first one first (empty for the other exits).
    triggers: tuple[str, ...] = ()


def take_profit_price(credit: float, params: StrategyParams) -> float:
    """Debit limit for the GTC buy-to-close order: keep `take_profit_pct` of the credit."""
    return round(credit * (1 - params.take_profit_pct), 2)


def stop_price(credit: float, params: StrategyParams) -> float:
    """Cost to close at which the position is bought back."""
    return round(credit * params.stop_loss_multiple, 2)


def expected_exit(mid: float, natural: float | None, params: StrategyParams) -> float:
    """Buy-back price to expect: the mid plus a share of the gap to the natural price."""
    if natural is None:
        return mid
    return mid + params.exit_slippage_share * max(natural - mid, 0.0)


def stop_reached(mid: float, natural: float | None, credit: float, params: StrategyParams) -> bool:
    return expected_exit(mid, natural, params) >= stop_price(credit, params)


def stop_triggers(
    position: ShortPremium, mark: float, today: date, params: StrategyParams, view: MarketView
) -> tuple[str, ...]:
    """Stop signals firing now. Missing data (no delta, no spot) never fires a signal."""
    out = []
    credit = position.credit
    if (
        params.use_stop_loss
        and stop_reached(mark, view.natural, credit, params)
        and view.confirmations >= params.stop_confirmations
    ):
        out.append(COST)
    if params.use_stop_delta and view.short_delta is not None:
        if abs(view.short_delta) >= params.stop_delta:
            out.append(DELTA)
    if params.use_stop_breach and view.spot is not None and view.short_strike is not None:
        if view.spot < view.short_strike * (1 - params.stop_breach_pct):
            out.append(BREACH)
    losing = mark > credit
    if params.use_stop_liquidity and losing and view.natural is not None and credit > 0:
        if (view.natural - mark) / credit >= params.max_exit_spread_pct:
            out.append(LIQUIDITY)
    if params.use_stop_event and view.next_event is not None:
        days = (view.next_event - today).days
        if 0 <= days <= params.event_exit_days and view.next_event <= position.expiration:
            out.append(EVENT)
    if params.use_stop_portfolio and view.portfolio_breach and losing:
        out.append(PORTFOLIO)
    return tuple(out)


def evaluate_exit(
    position: ShortPremium,
    mark: float,
    today: date,
    params: StrategyParams,
    view: MarketView | None = None,
) -> ExitSignal | None:
    """Return the exit to execute now, if any, given the current cost to close (mid).

    Covered calls have no stop: the shares cover the call, and being called away is part of
    the wheel. A True Wheel put has neither stop nor time exit: it may be assigned. The stop
    is checked first so a losing position at 21 DTE is tagged as a stop.
    A rule switched off in the parameters never fires.

    Without a `view` (the daily backtest), the stop compares the mark with the level, alone.
    """
    held = position.strategy == "covered_call" or position.assignment_accepted
    if not held and view is not None:
        triggers = stop_triggers(position, mark, today, params, view)
        if triggers:
            return ExitSignal(STOP_LOSS, mark, triggers)
    elif params.use_stop_loss and not held and mark >= stop_price(position.credit, params):
        return ExitSignal(STOP_LOSS, mark, (COST,))
    if params.use_take_profit and mark <= take_profit_price(position.credit, params):
        return ExitSignal(PROFIT_TARGET, mark)
    if (
        params.use_time_exit
        and not position.assignment_accepted
        and (position.expiration - today).days <= params.exit_dte
    ):
        return ExitSignal(TIME_EXIT, mark)
    return None


def stop_rules(
    position: ShortPremium, params: StrategyParams, short_strike: float | None
) -> list[dict[str, str]]:
    """The stop signals switched on for a position, in words (empty when it is never stopped)."""
    if position.strategy == "covered_call" or position.assignment_accepted:
        return []
    rules = []
    if params.use_stop_loss:
        rules.append(
            (
                COST,
                f"rachat attendu (mid + {params.exit_slippage_share:.0%} de l'écart jusqu'au "
                f"naturel) à {stop_price(position.credit, params):.2f} ou plus, sur "
                f"{params.stop_confirmations} relevé(s) de suite",
            )
        )
    if params.use_stop_delta:
        rules.append((DELTA, f"delta de la jambe vendue à {params.stop_delta:.2f} ou plus"))
    if params.use_stop_breach and short_strike is not None:
        level = short_strike * (1 - params.stop_breach_pct)
        rules.append((BREACH, f"sous-jacent sous {level:.2f}"))
    if params.use_stop_liquidity:
        rules.append(
            (
                LIQUIDITY,
                f"en perte, écart naturel - mid à {params.max_exit_spread_pct:.0%} du crédit "
                "ou plus",
            )
        )
    if params.use_stop_event:
        rules.append((EVENT, f"résultats dans {params.event_exit_days} jour(s) ou moins"))
    if params.use_stop_portfolio:
        rules.append(
            (PORTFOLIO, f"drawdown du compte à {params.max_drawdown_pct:.0%} : la plus perdante")
        )
    return [{"key": key, "label": TRIGGER_LABELS[key], "rule": rule} for key, rule in rules]
