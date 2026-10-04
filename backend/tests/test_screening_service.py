from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.market import MarketSnapshot
from app.models import IvHistory, Opportunity, Position, PositionLeg, StrategyConfig
from app.models.enums import (
    InstrumentType,
    OpportunityStatus,
    PositionStatus,
    Side,
    StrategyType,
)
from app.services.screening import run_screener
from tests.chains import TODAY, make_snapshot


class FakeProvider:
    def __init__(self, *snapshots: MarketSnapshot) -> None:
        self.snapshots = {s.symbol: s for s in snapshots}

    def snapshot(self, symbol: str, today) -> MarketSnapshot:
        if symbol not in self.snapshots:
            raise LookupError(f"no data for {symbol}")
        return self.snapshots[symbol]


def test_run_stores_deals_and_iv_and_expires_the_previous_proposals(session: Session) -> None:
    provider = FakeProvider(make_snapshot("SPY", 500, 5), make_snapshot("QQQ", 400, 5))

    first = run_screener(session, provider, TODAY, starting_capital=20_000)

    config = session.scalar(select(StrategyConfig).where(StrategyConfig.is_active))
    assert config is not None and config.version == 1 and config.params["max_deals"] == 5
    assert first.filter_counts["selected"] == 2
    missing = {*config.params["large_caps"], *config.params["wheel"], "IWM"}
    assert set(first.filter_counts["errors"]) == missing

    query = select(Opportunity).where(Opportunity.screener_run_id == first.id)
    deals = session.scalars(query).all()
    assert {d.underlying for d in deals} == {"SPY", "QQQ"}
    deal = deals[0]
    assert deal.strategy_type == StrategyType.PUT_CREDIT_SPREAD
    assert deal.status == OpportunityStatus.PROPOSED
    assert len(deal.legs) == 2 and {leg.side for leg in deal.legs} == {Side.SELL, Side.BUY}
    quantity = deal.legs[0].quantity
    assert quantity == deal.metrics["quantity"] > 0
    assert deal.collateral == deal.max_loss <= Decimal("2000")
    assert deal.metrics["take_profit_price"] == pytest.approx(float(deal.credit) / 2, abs=0.01)
    assert deal.metrics["iv_rank_method"] == "hv_proxy"

    iv_rows = session.scalars(select(IvHistory).where(IvHistory.day == TODAY)).all()
    assert {row.underlying for row in iv_rows} == {"SPY", "QQQ"}

    second = run_screener(session, provider, TODAY + timedelta(days=1), starting_capital=20_000)
    session.expire_all()
    statuses = set(
        session.execute(select(Opportunity.screener_run_id, Opportunity.status).distinct()).all()
    )
    assert statuses == {
        (first.id, OpportunityStatus.EXPIRED),
        (second.id, OpportunityStatus.PROPOSED),
    }


def test_assigned_shares_get_a_covered_call_and_no_new_put(session: Session) -> None:
    session.add(
        Position(
            underlying="SOFI",
            status=PositionStatus.OPEN,
            collateral=Decimal("3100"),
            legs=[
                PositionLeg(
                    instrument_type=InstrumentType.STOCK,
                    symbol="SOFI",
                    side=Side.BUY,
                    quantity=200,
                    avg_price=Decimal("15.5"),
                )
            ],
        )
    )
    session.flush()
    later = TODAY + timedelta(days=90)
    provider = FakeProvider(
        make_snapshot("SOFI", 15, 0.5, iv=0.50, next_earnings=later, option_types=("put", "call"))
    )

    run = run_screener(session, provider, TODAY, starting_capital=20_000)

    deals = session.scalars(select(Opportunity).where(Opportunity.screener_run_id == run.id)).all()
    assert [d.strategy_type for d in deals] == [StrategyType.COVERED_CALL]
    assert deals[0].legs[0].quantity == 2
    assert deals[0].legs[0].strike >= Decimal("15.5")
    assert run.filter_counts["covered_calls"] == 1
    assert run.filter_counts["skipped"] == {"SOFI": "already_open"}
