"""Safety checks that gate new entries, and the account value losses are measured on.

New entries are blocked while any check fails: the worker or the position monitor has not run
recently, the day's data is missing, the broker is unreachable or the market closed, or the
daily, monthly or drawdown loss limit is reached. The same checks feed the status banner.
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.params import StrategyParams
from app.domain.risk import Exposure, shares_stress_loss, stress_move
from app.models import AccountSnapshot, JobHeartbeat, Position, PositionMark, ScreenerRun
from app.models.enums import InstrumentType, PositionStatus, StrategyType

MARKET_TZ = ZoneInfo("America/New_York")
ACTIVE = (PositionStatus.PENDING, PositionStatus.OPEN)
TICK, MONITOR, SCREENER = "tick", "monitor", "screener"


def _now() -> datetime:
    return datetime.now(UTC)


# --- job heartbeats ---------------------------------------------------------------------------


def record_job(
    session: Session,
    job: str,
    ok: bool,
    error: str | None = None,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> JobHeartbeat:
    """Store the outcome of one run of a worker job (the caller commits)."""
    now = now or _now()
    row = session.get(JobHeartbeat, job)
    if row is None:
        row = JobHeartbeat(job=job)
        session.add(row)
    row.last_started_at = now
    if ok:
        row.last_success_at = now
        row.last_error = None
    else:
        row.last_failure_at = now
        row.last_error = (error or "échec")[:500]
    row.details = details
    session.flush()
    return row


def heartbeats(session: Session) -> dict[str, JobHeartbeat]:
    return {row.job: row for row in session.scalars(select(JobHeartbeat)).all()}


# --- exposures and account value ---------------------------------------------------------------


def open_exposures(session: Session, params: StrategyParams) -> list[Exposure]:
    """Open and pending positions as the risk limits see them (totals per position).

    Rows written before stress losses were stored get one estimated from their collateral.
    """
    positions = session.scalars(select(Position).where(Position.status.in_(ACTIVE))).all()
    out = []
    for p in positions:
        strategy = p.strategy_type.value if p.strategy_type else None
        collateral = float(p.collateral or 0)
        move = stress_move(p.underlying, p.sector, params)
        options = [leg for leg in p.legs if leg.instrument_type == InstrumentType.OPTION]
        if p.strategy_type == StrategyType.COVERED_CALL:
            max_loss, stress = 0.0, 0.0
        elif strategy is None:  # shares held after an assignment: stock to zero
            max_loss = float(p.max_loss) if p.max_loss is not None else collateral
            stress = (
                float(p.stress_loss)
                if p.stress_loss is not None
                else shares_stress_loss(collateral, 1, move)
            )
        else:
            max_loss = float(p.max_loss) if p.max_loss is not None else collateral
            if p.stress_loss is not None:
                stress = float(p.stress_loss)
            elif p.strategy_type == StrategyType.PUT_CREDIT_SPREAD:
                stress = max_loss
            else:
                stress = min(max_loss, collateral * move)
        out.append(
            Exposure(
                underlying=p.underlying,
                sector=p.sector,
                strategy=strategy,
                max_loss=max_loss,
                stress_loss=stress,
                collateral=collateral,
                expiration=options[0].expiration if options else None,
            )
        )
    return out


def realized_capital(session: Session, starting_capital: float) -> float:
    realized = session.scalar(select(func.coalesce(func.sum(Position.realized_pnl), 0)))
    return float(starting_capital) + float(realized)


def unrealized_pnl(session: Session) -> float:
    """Sum of the last marks of open option positions (shares are not marked: no stock quote)."""
    open_ids = session.scalars(
        select(Position.id).where(
            Position.status == PositionStatus.OPEN, Position.strategy_type.is_not(None)
        )
    ).all()
    if not open_ids:
        return 0.0
    query = (
        select(PositionMark.unrealized_pnl)
        .where(PositionMark.position_id.in_(open_ids))
        .distinct(PositionMark.position_id)
        .order_by(PositionMark.position_id, PositionMark.marked_at.desc())
    )
    return float(sum(session.scalars(query).all()))


def account_value(session: Session, starting_capital: float) -> float:
    return realized_capital(session, starting_capital) + unrealized_pnl(session)


def record_equity(session: Session, starting_capital: float, day: date) -> float:
    """Upsert today's account snapshot with the current value (the monitor calls it)."""
    capital = realized_capital(session, starting_capital)
    equity = capital + unrealized_pnl(session)
    engaged = float(
        session.scalar(
            select(func.coalesce(func.sum(Position.collateral), 0)).where(
                Position.status.in_(ACTIVE)
            )
        )
    )
    values = {
        "equity": round(equity, 4),
        "cash": round(capital - engaged, 4),
        "collateral_used": round(engaged, 4),
        "buying_power": round(capital - engaged, 4),
    }
    stmt = insert(AccountSnapshot).values(day=day, **values)
    session.execute(stmt.on_conflict_do_update(index_elements=["day"], set_=values))
    return equity


@dataclass(frozen=True)
class Losses:
    equity: float
    daily: float  # share of the previous close's value, negative for a loss
    monthly: float
    drawdown: float  # share of the peak, positive


def losses(
    session: Session, starting_capital: float, today: date, equity: float | None = None
) -> Losses:
    """Daily, monthly and peak-to-now changes of the account value.

    Baselines: the last snapshot before today, the last one before the month, the highest
    one ever; the starting capital when there is none yet.
    """
    if equity is None:
        equity = account_value(session, starting_capital)
    start = float(starting_capital)

    def before(day: date) -> float:
        value = session.scalar(
            select(AccountSnapshot.equity)
            .where(AccountSnapshot.day < day)
            .order_by(AccountSnapshot.day.desc())
            .limit(1)
        )
        return float(value) if value is not None else start

    previous = before(today)
    month_start = before(today.replace(day=1))
    peak = max(
        start,
        equity,
        float(session.scalar(select(func.max(AccountSnapshot.equity))) or start),
    )
    return Losses(
        equity=equity,
        daily=equity / previous - 1 if previous > 0 else 0.0,
        monthly=equity / month_start - 1 if month_start > 0 else 0.0,
        drawdown=(peak - equity) / peak if peak > 0 else 0.0,
    )


# --- the entry gate ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Gate:
    checks: list[Check] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [c.detail for c in self.checks if not c.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "checks": [asdict(c) for c in self.checks],
        }


def _age(moment: datetime | None, now: datetime) -> timedelta | None:
    return None if moment is None else now - moment


def _minutes(delta: timedelta | None) -> str:
    if delta is None:
        return "jamais"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return f"il y a {minutes} min"
    return f"il y a {minutes // 60} h"


def entry_gate(
    session: Session,
    params: StrategyParams,
    starting_capital: float,
    now: datetime | None = None,
    market_open: bool | None = None,
    broker_error: str | None = None,
    risk_increasing: bool = True,
) -> Gate:
    """Every check new entries depend on.

    `market_open` comes from a live broker call when the caller made one (acceptance), else
    from the last monitor pass. Loss limits only gate entries that add risk (`risk_increasing`
    is False for a covered call on shares already held).
    """
    now = now or _now()
    today = now.astimezone(MARKET_TZ).date()
    beats = heartbeats(session)
    checks: list[Check] = []

    last_any = max((b.last_success_at for b in beats.values() if b.last_success_at), default=None)
    worker_age = _age(last_any, now)
    worker_ok = worker_age is not None and worker_age <= timedelta(
        minutes=params.max_worker_age_minutes
    )
    checks.append(
        Check(
            "worker",
            "Worker",
            worker_ok,
            f"Worker : dernier passage réussi {_minutes(worker_age)}"
            + ("" if worker_ok else f" (max {params.max_worker_age_minutes} min)"),
        )
    )

    monitor = beats.get(MONITOR)
    monitor_age = _age(monitor.last_success_at if monitor else None, now)
    failing = bool(
        monitor
        and monitor.last_failure_at
        and (monitor.last_success_at is None or monitor.last_failure_at > monitor.last_success_at)
    )
    monitor_ok = (
        not failing
        and monitor_age is not None
        and monitor_age <= timedelta(minutes=params.max_monitor_age_minutes)
    )
    detail = f"Moniteur : dernier passage réussi {_minutes(monitor_age)}"
    if failing:
        detail += f", dernier passage en échec ({monitor.last_error})"
    elif not monitor_ok:
        detail += f" (max {params.max_monitor_age_minutes} min)"
    checks.append(Check("monitor", "Moniteur des positions", monitor_ok, detail))

    run = session.execute(
        select(ScreenerRun.started_at, ScreenerRun.finished_at)
        .order_by(ScreenerRun.started_at.desc())
        .limit(1)
    ).first()
    run_day = run.started_at.astimezone(MARKET_TZ).date() if run else None
    data_ok = run is not None and run.finished_at is not None and run_day == today
    checks.append(
        Check(
            "data",
            "Données du jour",
            data_ok,
            "Données : screener du jour terminé"
            if data_ok
            else (
                "Données obsolètes : pas de screener terminé aujourd'hui"
                + (f" (dernier le {run_day:%d/%m})" if run_day else "")
            ),
        )
    )

    details = (monitor.details or {}) if monitor else {}
    if broker_error is None and monitor is not None and details.get("broker_ok") is False:
        broker_error = details.get("broker_error") or "échec au dernier passage du moniteur"
    if market_open is None:
        market_open = details.get("market_open")
    broker_ok = broker_error is None and market_open is not None
    checks.append(
        Check(
            "broker",
            "Courtier",
            broker_ok,
            "Courtier Alpaca paper joignable"
            if broker_ok
            else f"Courtier indisponible : {broker_error or 'état inconnu'}",
        )
    )
    checks.append(
        Check(
            "market",
            "Marché",
            bool(market_open),
            "Marché ouvert" if market_open else "Marché fermé : pas de nouvelle entrée",
        )
    )

    if risk_increasing:
        state = losses(session, starting_capital, today)
        for key, label, value, limit in (
            ("daily_loss", "Perte du jour", -state.daily, params.max_daily_loss_pct),
            ("monthly_loss", "Perte du mois", -state.monthly, params.max_monthly_loss_pct),
            ("drawdown", "Drawdown", state.drawdown, params.max_drawdown_pct),
        ):
            ok = value < limit
            checks.append(
                Check(
                    key,
                    label,
                    ok,
                    f"{label} : {max(value, 0):.1%} (limite {limit:.1%})"
                    + ("" if ok else ", limite atteinte"),
                )
            )
    return Gate(checks)


def status(
    session: Session, params: StrategyParams, starting_capital: float, now: datetime | None = None
) -> dict[str, Any]:
    """What the permanent banner shows."""
    now = now or _now()
    beats = heartbeats(session)
    gate = entry_gate(session, params, starting_capital, now)
    monitor = beats.get(MONITOR)
    last_run = session.scalar(select(func.max(ScreenerRun.finished_at)))
    last_mark = session.scalar(select(func.max(PositionMark.marked_at)))
    last_any = max((b.last_success_at for b in beats.values() if b.last_success_at), default=None)
    data_update = max((t for t in (last_run, last_mark) if t is not None), default=None)
    return {
        "environment": "PAPER",
        "trading_allowed": gate.allowed,
        "gate": gate.to_dict(),
        "worker_last_success": last_any.isoformat() if last_any else None,
        "monitor_last_success": (
            monitor.last_success_at.isoformat() if monitor and monitor.last_success_at else None
        ),
        "monitor_last_error": monitor.last_error if monitor else None,
        "data_last_update": data_update.isoformat() if data_update else None,
        "screener_last_run": last_run.isoformat() if last_run else None,
    }


def share_lot_risk(cost_basis: float, shares: int, params: StrategyParams, symbol: str) -> float:
    """Stress loss of shares received on assignment."""
    return shares_stress_loss(cost_basis, shares, stress_move(symbol, None, params))
