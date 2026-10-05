"""Daily screener run: market data in, screener_runs, opportunities and iv_history out."""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.exits import stop_price, take_profit_price
from app.domain.market import MarketSnapshot
from app.domain.params import TRUE_WHEEL_GROUP, StrategyParams
from app.domain.risk import NO_TRADE_MESSAGES, AccountState, cluster_of
from app.domain.screener import Candidate, ScreenResult, ShareLot, screen
from app.models import (
    IvHistory,
    Opportunity,
    OpportunityLeg,
    Position,
    PositionLeg,
    ScreenerRun,
    StrategyConfig,
)
from app.models.enums import (
    InstrumentType,
    OpportunityStatus,
    OptionType,
    PositionStatus,
    Side,
    StrategyType,
)
from app.services.safety import SCREENER, open_exposures, record_job

logger = logging.getLogger(__name__)

ACTIVE = (PositionStatus.PENDING, PositionStatus.OPEN)
MARKET_TZ = ZoneInfo("America/New_York")


class SnapshotProvider(Protocol):
    def snapshot(self, symbol: str, today: date) -> MarketSnapshot: ...


def _dec(value: float | None, places: int = 4) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, places)))


def active_config(session: Session) -> StrategyConfig:
    """The active parameter version; the defaults become version 1 on the first run."""
    config = session.scalar(select(StrategyConfig).where(StrategyConfig.is_active))
    if config is None:
        version = (session.scalar(select(func.max(StrategyConfig.version))) or 0) + 1
        config = StrategyConfig(version=version, params=StrategyParams().to_dict(), is_active=True)
        session.add(config)
        session.flush()
    return config


def account_state(session: Session, starting_capital: float) -> AccountState:
    realized = session.scalar(select(func.coalesce(func.sum(Position.realized_pnl), 0)))
    engaged = session.scalar(
        select(func.coalesce(func.sum(Position.collateral), 0)).where(Position.status.in_(ACTIVE))
    )
    return AccountState(float(starting_capital) + float(realized), float(engaged))


def share_lots(session: Session) -> list[ShareLot]:
    """Open share positions (from assignment) minus shares already covered by a call."""
    covered: dict[int, int] = dict(
        session.execute(
            select(Position.parent_position_id, func.sum(PositionLeg.quantity * 100))
            .join(PositionLeg)
            .where(
                Position.status.in_(ACTIVE),
                Position.strategy_type == StrategyType.COVERED_CALL,
                Position.parent_position_id.is_not(None),
            )
            .group_by(Position.parent_position_id)
        ).all()
    )
    rows = session.execute(
        select(Position.id, Position.underlying, PositionLeg.quantity, PositionLeg.avg_price)
        .join(PositionLeg)
        .where(
            Position.status == PositionStatus.OPEN,
            PositionLeg.instrument_type == InstrumentType.STOCK,
            PositionLeg.side == Side.BUY,
        )
    ).all()
    return [
        ShareLot(underlying, quantity - covered.get(pid, 0), float(avg_price or 0), pid)
        for pid, underlying, quantity, avg_price in rows
        if quantity - covered.get(pid, 0) >= 100
    ]


def open_underlyings(session: Session) -> set[str]:
    return set(
        session.scalars(select(Position.underlying).where(Position.status.in_(ACTIVE))).all()
    )


def cooling_down(session: Session, today: date, days: int) -> set[str]:
    """Underlyings whose last position closed less than `days` market days ago (calendar)."""
    if days <= 0:
        return set()
    rows = session.execute(
        select(Position.underlying, func.max(Position.closed_at))
        .where(Position.closed_at.is_not(None))
        .group_by(Position.underlying)
    ).all()
    return {
        underlying
        for underlying, closed_at in rows
        if (today - closed_at.astimezone(MARKET_TZ).date()).days < days
    }


def iv_history(session: Session, today: date) -> dict[str, list[float]]:
    rows = session.execute(
        select(IvHistory.underlying, IvHistory.iv30)
        .where(
            IvHistory.day >= today - timedelta(days=365),
            IvHistory.day < today,
            IvHistory.iv30.is_not(None),
        )
        .order_by(IvHistory.day)
    ).all()
    history: dict[str, list[float]] = {}
    for underlying, iv30 in rows:
        history.setdefault(underlying, []).append(float(iv30))
    return history


def _record_iv(session: Session, result: ScreenResult, today: date) -> None:
    for symbol, stats in result.underlyings.items():
        values = {
            "close": _dec(stats.spot),
            "iv30": _dec(stats.iv30, 6),
            "hv30": _dec(stats.hv30, 6),
        }
        stmt = insert(IvHistory).values(underlying=symbol, day=today, **values)
        session.execute(
            stmt.on_conflict_do_update(index_elements=["underlying", "day"], set_=values)
        )


def _opportunity(
    c: Candidate, run: ScreenerRun, config: StrategyConfig, params: StrategyParams
) -> Opportunity:
    """Credit is per share (the order's limit price); max loss and collateral cover the quantity."""
    held = c.strategy == "covered_call" or c.group == TRUE_WHEEL_GROUP
    metrics: dict[str, Any] = {
        "group": c.group,
        "quantity": c.quantity,
        "natural_credit": round(c.natural_credit, 4),
        "credit_total": round(c.credit * 100 * c.quantity, 2),
        "max_loss_per_unit": round(c.max_loss, 2),
        "collateral_per_unit": round(c.collateral, 2),
        "stress_loss_per_unit": round(c.stress_loss, 2),
        "holding_window": c.holding_window,
        "distance_pct": round(c.distance_pct, 4),
        "assignment_accepted": c.group == TRUE_WHEEL_GROUP,
        "distance_sd": None if c.distance_sd is None else round(c.distance_sd, 3),
        "abs_delta": round(c.abs_delta, 4),
        "cluster": cluster_of(c.underlying, c.sector, params),
        "sizing": c.sizing.to_dict() if c.sizing else None,
        "iv30": c.iv30,
        "hv30": c.hv30,
        "iv_rank_method": c.iv_rank.method if c.iv_rank else None,
        "iv_rank_days": c.iv_rank.days if c.iv_rank else None,
        "take_profit_price": (
            take_profit_price(c.credit, params) if params.use_take_profit else None
        ),
        "stop_price": (stop_price(c.credit, params) if params.use_stop_loss and not held else None),
        # A True Wheel put may be assigned: no stop and no time exit.
        "time_exit_date": (
            (c.expiration - timedelta(days=params.exit_dte)).isoformat()
            if params.use_time_exit and c.group != TRUE_WHEEL_GROUP
            else None
        ),
    }
    return Opportunity(
        screener_run_id=run.id,
        strategy_config_id=config.id,
        underlying=c.underlying,
        sector=c.sector,
        strategy_type=StrategyType(c.strategy),
        status=OpportunityStatus.PROPOSED,
        expiration=c.expiration,
        dte=c.dte,
        underlying_price=_dec(c.spot),
        credit=_dec(c.credit),
        max_loss=_dec(c.max_loss * c.quantity, 2),
        collateral=_dec(c.collateral * c.quantity, 2),
        stress_loss=_dec(c.stress_loss * c.quantity, 2),
        breakeven=_dec(c.breakeven),
        short_delta=_dec(c.short_delta, 6),
        pop=_dec(c.pop, 6),
        iv_rank=_dec(c.iv_rank.value if c.iv_rank else None, 6),
        iv_hv_ratio=_dec(c.iv_hv_ratio, 6),
        aroc=_dec(c.aroc, 6),
        score=_dec(c.score, 6),
        next_earnings=c.next_earnings,
        metrics=metrics,
        legs=[
            OpportunityLeg(
                option_symbol=leg.quote.symbol,
                option_type=OptionType(leg.quote.option_type),
                side=Side(leg.side),
                strike=_dec(leg.quote.strike),
                quantity=c.quantity,
                bid=_dec(leg.quote.bid),
                ask=_dec(leg.quote.ask),
                delta=_dec(leg.delta, 6),
                open_interest=leg.quote.open_interest,
                volume=leg.quote.volume,
            )
            for leg in c.legs
        ],
    )


def run_screener(
    session: Session,
    provider: SnapshotProvider,
    today: date,
    starting_capital: float,
) -> ScreenerRun:
    """Screen the universe, expire yesterday's unanswered proposals and store today's deals.

    The caller commits.
    """
    config = active_config(session)
    params = StrategyParams.from_dict(config.params)
    run = ScreenerRun(strategy_config_id=config.id, started_at=datetime.now(UTC))
    session.add(run)
    session.flush()

    lots = share_lots(session)
    symbols = list(dict.fromkeys([*params.universe, *(lot.underlying for lot in lots)]))
    snapshots, errors = [], {}
    for symbol in symbols:
        try:
            snapshots.append(provider.snapshot(symbol, today))
        except Exception as exc:  # one bad ticker must not sink the run
            logger.warning("snapshot %s failed: %s", symbol, exc)
            errors[symbol] = str(exc)[:200]

    result = screen(
        snapshots,
        today,
        params,
        account_state(session, starting_capital),
        iv_history(session, today),
        lots,
        open_underlyings(session),
        open_exposures(session, params),
        cooling_down(session, today, params.reentry_cooldown_days),
    )

    session.execute(
        update(Opportunity)
        .where(Opportunity.status == OpportunityStatus.PROPOSED)
        .values(status=OpportunityStatus.EXPIRED)
    )
    for c in [*result.selected, *result.covered_calls]:
        session.add(_opportunity(c, run, config, params))
    _record_iv(session, result, today)

    run.universe_size = len(symbols)
    run.filter_counts = {
        **result.funnel,
        "covered_calls": len(result.covered_calls),
        "skipped": result.skipped,
        # Exact NO TRADE reason of every ranked candidate that got no contract.
        "no_trade": {
            symbol: NO_TRADE_MESSAGES.get(code, code) for symbol, code in result.skipped.items()
        },
        "errors": errors,
    }
    run.finished_at = datetime.now(UTC)
    record_job(
        session,
        SCREENER,
        ok=len(errors) < len(symbols) or not symbols,
        error=None if len(errors) < len(symbols) else "aucune donnée de marché reçue",
        details={"run_id": run.id, "errors": len(errors), "selected": len(result.selected)},
    )
    session.flush()
    return run
