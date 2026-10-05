"""The entry gate: nothing is sent while the worker, the data, the broker or a loss limit fails."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session
from app.domain.orders import Quote
from app.domain.params import StrategyParams
from app.main import app
from app.models import AccountSnapshot, JobHeartbeat, Opportunity, Position, ScreenerRun
from app.models.enums import OpportunityStatus, OptionType, PositionStatus, Side, StrategyType
from app.services import safety
from app.services.safety import MONITOR, entry_gate, record_job
from app.services.trading import DecisionError, accept_opportunity, monitor
from tests.fake_broker import FakeBroker
from tests.test_trading_service import (
    LONG,
    SHORT,
    SOFI_CALL,
    healthy,
    make_opportunity,
    spread,
)

PARAMS = StrategyParams()
NOW = datetime.now(UTC)


def blocked(session: Session, broker: FakeBroker, opportunity_id: int) -> str:
    with pytest.raises(DecisionError, match="Entrée bloquée") as error:
        accept_opportunity(session, broker, opportunity_id, "click-0001", 20_000)
    assert not broker.requests
    assert session.get(Opportunity, opportunity_id).status == OpportunityStatus.PROPOSED
    return str(error.value)


def test_a_healthy_setup_lets_the_entry_through(session: Session) -> None:
    broker = FakeBroker()
    deal = spread(session)
    healthy(session, broker, deal.id)
    gate = entry_gate(session, PARAMS, 20_000, market_open=True)
    assert gate.allowed, gate.reasons
    accept_opportunity(session, broker, deal.id, "click-0001", 20_000)
    assert len(broker.requests) == 1


def test_worker_never_ran_blocks_entries(session: Session) -> None:
    broker = FakeBroker()
    deal = spread(session)
    broker.quotes = {SHORT: Quote(2.0, 2.1), LONG: Quote(1.0, 1.1)}
    message = blocked(session, broker, deal.id)
    assert "Worker : dernier passage réussi jamais" in message
    assert "Moniteur" in message


def test_a_stale_monitor_or_a_failing_one_blocks_entries(session: Session) -> None:
    broker = FakeBroker()
    deal = spread(session)
    healthy(session, broker, deal.id)
    session.get(JobHeartbeat, MONITOR).last_success_at = NOW - timedelta(minutes=45)
    assert "max 30 min" in blocked(session, broker, deal.id)

    record_job(session, MONITOR, ok=True)
    record_job(session, MONITOR, ok=False, error="étapes en échec : orders")
    assert "dernier passage en échec" in blocked(session, broker, deal.id)


def test_stale_data_blocks_entries(session: Session) -> None:
    broker = FakeBroker()
    deal = spread(session)
    healthy(session, broker, deal.id)
    run = session.get(ScreenerRun, deal.screener_run_id)
    run.started_at = run.finished_at = NOW - timedelta(days=2)
    session.flush()
    assert "Données obsolètes : pas de screener terminé aujourd'hui" in blocked(
        session, broker, deal.id
    )


def test_quotes_that_moved_or_are_missing_block_entries(session: Session) -> None:
    broker = FakeBroker()
    deal = spread(session)  # proposed at a 1.00 credit
    healthy(session, broker)
    broker.quotes = {SHORT: Quote(1.6, 1.7), LONG: Quote(1.0, 1.1)}  # 0.60 now
    assert "crédit coté 0.60 contre 1.00" in blocked(session, broker, deal.id)
    broker.quotes = {SHORT: Quote(2.0, 2.1)}
    assert f"Donnée absente : pas de cotation pour {LONG}" in blocked(session, broker, deal.id)


def test_broker_unavailable_or_market_closed_blocks_entries(session: Session) -> None:
    broker = FakeBroker()
    deal = spread(session)
    healthy(session, broker, deal.id)
    broker.down = True
    assert "Courtier indisponible : Alpaca injoignable" in blocked(session, broker, deal.id)
    broker.down, broker.is_open = False, False
    assert "Marché fermé" in blocked(session, broker, deal.id)


def _loss(session: Session, pnl: float) -> None:
    session.add(
        Position(
            underlying="XLE",
            status=PositionStatus.CLOSED,
            collateral=Decimal("0"),
            realized_pnl=Decimal(str(pnl)),
            closed_at=NOW,
        )
    )
    session.flush()


def test_daily_monthly_and_drawdown_limits_block_entries(session: Session) -> None:
    broker = FakeBroker()
    deal = spread(session)
    healthy(session, broker, deal.id)
    today = NOW.astimezone(safety.MARKET_TZ).date()
    session.add(AccountSnapshot(**_snapshot(today - timedelta(days=1), 20_000)))
    _loss(session, -500)  # 2.5 % in a day, over the 2 % limit
    message = blocked(session, broker, deal.id)
    assert "Perte du jour : 2.5% (limite 2.0%), limite atteinte" in message

    state = safety.losses(session, 20_000, today)
    assert state.daily == pytest.approx(-0.025) and state.equity == 19_500
    # A peak at 22 000 puts the account 11 % below it: over the 10 % drawdown limit.
    session.add(AccountSnapshot(**_snapshot(today - timedelta(days=40), 22_000)))
    session.flush()
    assert "Drawdown : 11.4% (limite 10.0%), limite atteinte" in blocked(session, broker, deal.id)


def _snapshot(day, equity: float) -> dict:
    value = Decimal(str(equity))
    return {
        "day": day,
        "equity": value,
        "cash": value,
        "collateral_used": Decimal("0"),
        "buying_power": value,
    }


def test_loss_limits_do_not_block_a_covered_call(session: Session) -> None:
    broker = FakeBroker()
    _loss(session, -3_000)  # 15 % down
    call = make_opportunity(
        session,
        StrategyType.COVERED_CALL,
        "SOFI",
        [(SOFI_CALL, OptionType.CALL, Side.SELL, 16, 0.28, 0.32)],
        0.30,
        1,
        0,
    )
    healthy(session, broker, call.id)
    gate = entry_gate(session, PARAMS, 20_000, market_open=True, risk_increasing=False)
    assert gate.allowed
    assert not entry_gate(session, PARAMS, 20_000, market_open=True).allowed


def test_the_monitor_records_its_pass_and_the_account_value(session: Session) -> None:
    broker = FakeBroker()
    today = NOW.astimezone(safety.MARKET_TZ).date()
    assert monitor(session, broker, today, 20_000)
    beat = session.get(JobHeartbeat, MONITOR)
    assert beat.last_success_at is not None and beat.details["market_open"] is True
    assert session.get(AccountSnapshot, today).equity == Decimal("20000")

    broker.down = True
    assert not monitor(session, broker, today, 20_000)
    beat = session.get(JobHeartbeat, MONITOR)
    assert beat.details["broker_ok"] is False and "exits" in beat.last_error
    gate = entry_gate(session, PARAMS, 20_000)
    assert not gate.allowed
    assert any("Courtier indisponible" in r for r in gate.reasons)


def test_status_banner(session: Session) -> None:
    app.dependency_overrides[get_session] = lambda: session
    try:
        status = TestClient(app).get("/status").json()
    finally:
        app.dependency_overrides.clear()
    assert status["environment"] == "PAPER" and status["profile_version"] >= 1
    assert status["trading_allowed"] is False
    assert {c["key"] for c in status["gate"]["checks"]} >= {
        "worker",
        "monitor",
        "data",
        "broker",
        "market",
        "daily_loss",
        "monthly_loss",
        "drawdown",
    }
