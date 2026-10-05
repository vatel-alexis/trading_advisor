from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session
from app.domain.orders import Quote
from app.main import app
from app.models import Position
from app.models.enums import ExitReason, PositionStatus, StrategyType
from app.services import views
from app.services.trading import close_position, sync_orders
from tests.fake_broker import FakeBroker
from tests.test_trading_service import LONG, SHORT, accept, open_spread, spread


def market_today() -> date:
    return datetime.now(views.MARKET_TZ).date()


def test_win_rate_premiums_and_curve_from_two_spreads(session: Session) -> None:
    broker = FakeBroker()
    winner = open_spread(session, broker)  # 1.05 credit x 2 contracts
    broker.fill(broker.last()[0], {SHORT: 0.60, LONG: 0.08})  # profit target at 0.52
    sync_orders(session, broker)

    loser = accept(session, broker, spread(session).id, "click-0002")
    broker.fill(broker.last()[0], {SHORT: 2.05, LONG: 1.0})
    sync_orders(session, broker)
    broker.quotes = {SHORT: Quote(1.9, 2.0), LONG: Quote(0.45, 0.5)}
    close_position(session, broker, loser.id, "close-0001")
    broker.fill(broker.last()[0], {SHORT: 2.0, LONG: 0.45})  # bought back at 1.55
    sync_orders(session, broker)
    session.flush()
    assert winner.realized_pnl == Decimal("106.00") and loser.realized_pnl == Decimal("-100.00")

    stats = views.analytics(session, 20_000, market_today())

    assert (stats["trades"], stats["wins"], stats["losses"]) == (2, 1, 1)
    assert stats["win_rate"] == 0.5 and stats["realized_pnl"] == 6.0
    assert stats["avg_win"] == 106.0 and stats["avg_loss"] == -100.0
    assert stats["profit_factor"] == 1.06
    assert stats["premium_collected"] == 420.0 and stats["premium_this_month"] == 420.0
    assert stats["exit_reasons"] == {"profit_target": 1, "manual": 1}
    [point] = stats["curve"]
    assert point["cumulative"] == 6.0 and point["equity"] == 20_006.0
    [month] = stats["months"]
    assert month == {
        "month": market_today().strftime("%Y-%m"),
        "premium": 420.0,
        "realized_pnl": 6.0,
        "trades": 2,
    }
    [row] = stats["by_strategy"]
    assert row["strategy"] == "put_credit_spread" and row["win_rate"] == 0.5


def _finished(
    session: Session,
    strategy: StrategyType | None,
    status: PositionStatus,
    pnl: str,
    closed_at: datetime | None,
    reason: ExitReason | None = None,
) -> Position:
    position = Position(
        underlying="SOFI",
        strategy_type=strategy,
        status=status,
        collateral=Decimal("0"),
        realized_pnl=Decimal(pnl),
        closed_at=closed_at,
        exit_reason=reason,
    )
    session.add(position)
    return position


def test_the_wheel_counts_once_and_the_curve_tracks_the_drawdown(session: Session) -> None:
    day1 = datetime(2026, 9, 1, 15, tzinfo=UTC)
    day2 = datetime(2026, 9, 2, 15, tzinfo=UTC)
    # The assigned put's premium sits in the lot's cost basis: it is not a trade of its own.
    _finished(
        session,
        StrategyType.CASH_SECURED_PUT,
        PositionStatus.ASSIGNED,
        "0",
        day1,
        ExitReason.ASSIGNMENT,
    )
    _finished(session, None, PositionStatus.CLOSED, "140", day1, ExitReason.CALLED_AWAY)
    _finished(
        session,
        StrategyType.COVERED_CALL,
        PositionStatus.ASSIGNED,
        "30",
        day1,
        ExitReason.CALLED_AWAY,
    )
    _finished(
        session,
        StrategyType.PUT_CREDIT_SPREAD,
        PositionStatus.CLOSED,
        "-300",
        day2,
        ExitReason.STOP_LOSS,
    )
    # A partial close on a position still open lands on today's date.
    _finished(session, StrategyType.PUT_CREDIT_SPREAD, PositionStatus.OPEN, "20", None)
    session.flush()

    stats = views.analytics(session, 20_000, date(2026, 9, 10))

    assert stats["trades"] == 3 and stats["wins"] == 2
    assert [p["equity"] for p in stats["curve"]] == [20_170.0, 19_870.0, 19_890.0]
    assert stats["curve"][-1]["date"] == "2026-09-10"
    assert stats["max_drawdown"] == 300.0 and stats["max_drawdown_pct"] == round(300 / 20_170, 4)
    by_strategy = {row["strategy"]: row for row in stats["by_strategy"]}
    assert set(by_strategy) == {"covered_call", "put_credit_spread", "shares"}
    assert by_strategy["shares"]["realized_pnl"] == 140.0
    assert stats["premium_collected"] == 0.0  # no fill events in this setup


def test_dashboard_route_carries_the_analytics(session: Session) -> None:
    app.dependency_overrides[get_session] = lambda: session
    try:
        board = TestClient(app).get("/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert board.status_code == 200
    stats = board.json()["analytics"]
    assert stats["trades"] == 0 and stats["win_rate"] is None and stats["curve"] == []
