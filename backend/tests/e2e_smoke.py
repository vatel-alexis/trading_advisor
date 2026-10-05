"""End-to-end check of the running stack (CI `e2e` workflow), after a live screener run.

    python -m tests.e2e_smoke [API_URL] [WEB_URL]

Plays one paper cycle against the stack's database with the in-memory broker (no order ever
reaches Alpaca): accept the best deal, fill it, fill the 50 % profit target, reject the next
deal. Then reads the API and the Next.js pages and fails on the first thing missing.
"""

import json
import sys
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.domain.orders import Quote
from app.models import Opportunity, Order, Position
from app.models.enums import (
    OpportunityStatus,
    OrderPurpose,
    PositionStatus,
    RejectReason,
    Side,
)
from app.services.trading import accept_opportunity, monitor, reject_opportunity, sync_orders
from tests.fake_broker import FakeBroker


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:
        assert resp.status == 200, f"{url} -> {resp.status}"
        return resp.read().decode()


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"ÉCHEC : {message}")
    print(f"ok  {message}")


def play_cycle() -> tuple[str, str]:
    """Accept, fill and take profit on the best deal; reject the second one."""
    broker = FakeBroker()
    with SessionLocal() as session:
        deals = session.scalars(
            select(Opportunity)
            .where(Opportunity.status == OpportunityStatus.PROPOSED)
            .order_by(Opportunity.score.desc())
        ).all()
        expect(len(deals) >= 2, f"le screener a proposé {len(deals)} deals (2 au moins)")
        best, second = deals[0], deals[1]

        # A monitor pass (in-memory broker, market open) and quotes for the deal: the entry
        # checks pass as they would with a healthy worker during the session.
        monitor(session, broker, datetime.now(ZoneInfo("America/New_York")).date(), None)
        for leg in best.legs:
            broker.quotes[leg.option_symbol] = Quote(float(leg.bid), float(leg.ask))
        position = accept_opportunity(
            session, broker, best.id, "e2e-accept-0001", get_settings().starting_capital
        )
        expect(position.status == PositionStatus.PENDING, f"{best.underlying} accepté, ordre parti")
        order_id, request = broker.last()
        mids = {
            leg.option_symbol: round((float(leg.bid) + float(leg.ask)) / 2, 2) for leg in best.legs
        }
        broker.fill(order_id, mids)
        sync_orders(session, broker)
        session.commit()
        expect(position.status == PositionStatus.OPEN, "ordre d'ouverture exécuté")

        target = session.scalars(
            select(Order).where(
                Order.position_id == position.id, Order.purpose == OrderPurpose.TAKE_PROFIT
            )
        ).one()
        expect(target.time_in_force == "gtc", f"prise de profit GTC à {target.limit_price}")
        target_id, _ = broker.last()
        # Buy back the short leg at the target debit; a spread's long leg is sold for nothing.
        broker.fill(
            target_id,
            {
                leg.option_symbol: float(target.limit_price) if leg.side == Side.SELL else 0.0
                for leg in best.legs
            },
        )
        sync_orders(session, broker)
        session.commit()
        session.refresh(position)
        expect(
            position.status == PositionStatus.CLOSED and float(position.realized_pnl) > 0,
            f"position fermée à la prise de profit, P&L {position.realized_pnl}",
        )

        reject_opportunity(
            session, second.id, "e2e-reject-0001", RejectReason.NO_CONVICTION, "test e2e"
        )
        session.commit()
        expect(True, f"{second.underlying} rejeté")
        return best.underlying, second.underlying


def check_api(api: str, closed: str, rejected: str) -> None:
    health = json.loads(fetch(f"{api}/health"))
    expect(health["database"] == "ok", f"API /health : {health}")
    board = json.loads(fetch(f"{api}/dashboard"))
    stats = board["analytics"]
    expect(
        stats["trades"] == 1 and stats["wins"] == 1 and stats["win_rate"] == 1,
        "analytics : 1 trade gagnant",
    )
    expect(stats["premium_collected"] > 0, f"primes collectées {stats['premium_collected']}")
    expect(stats["curve"] and stats["curve"][-1]["equity"] > board["starting_capital"], "courbe")
    expect(board["last_screener_run"] is not None, f"dernier screener {board['last_screener_run']}")
    history = json.loads(fetch(f"{api}/history"))
    kinds = {(row["kind"], row["underlying"]) for row in history["rows"]}
    expect(("closed", closed) in kinds and ("rejected", rejected) in kinds, "historique")


def check_web(web: str, closed: str, rejected: str) -> None:
    pages = {
        "/": ["Tableau de bord", "Performance", "1 gagnant sur 1 trade"],
        "/opportunites": ["Opportunités du jour"],
        "/positions": ["Positions ouvertes"],
        "/historique": ["Historique", closed, rejected],
    }
    with SessionLocal() as session:
        proposed = session.scalars(
            select(Opportunity.underlying).where(Opportunity.status == OpportunityStatus.PROPOSED)
        ).all()
    if proposed:
        pages["/opportunites"].append(proposed[0])
    for path, markers in pages.items():
        html = fetch(f"{web}{path}")
        expect("API injoignable" not in html, f"page {path} servie avec l'API")
        missing = [m for m in markers if m not in html]
        expect(not missing, f"page {path} : {markers}" + (f", manque {missing}" if missing else ""))


def main(api: str, web: str) -> None:
    print(f"Cycle paper simulé le {date.today()} (broker en mémoire, aucun ordre vers Alpaca)")
    closed, rejected = play_cycle()
    check_api(api, closed, rejected)
    check_web(web, closed, rejected)
    with SessionLocal() as session:
        left = session.scalars(select(Position)).all()
    expect(len(left) == 1, "une seule position créée par le test")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(
        args[0] if args else "http://localhost:8000",
        args[1] if len(args) > 1 else "http://localhost:3000",
    )
