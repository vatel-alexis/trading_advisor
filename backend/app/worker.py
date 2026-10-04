"""Scheduled jobs, run in their own process (never inside the API's uvicorn workers).

Jobs are registered here as sprints land: daily screener (~10:30 ET), position monitor
(every 5 min on weekdays: order sync, assignments, exits), then end-of-day snapshots.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from app.broker.alpaca import AlpacaBroker
from app.config import get_settings
from app.db import SessionLocal
from app.domain.params import StrategyParams
from app.marketdata.yahoo import YahooProvider
from app.services.screening import active_config, run_screener
from app.services.trading import monitor

MARKET_TZ = "America/New_York"

logger = logging.getLogger("worker")


def heartbeat() -> None:
    logger.info("worker alive")


def daily_screener() -> None:
    today = datetime.now(ZoneInfo(MARKET_TZ)).date()
    with SessionLocal() as session:
        params = StrategyParams.from_dict(active_config(session).params)
        provider = YahooProvider(params.dte_min, params.dte_max)
        run = run_screener(session, provider, today, get_settings().starting_capital)
        session.commit()
    logger.info("screener run %s: %s", run.id, run.filter_counts)


def position_monitor() -> None:
    today = datetime.now(ZoneInfo(MARKET_TZ)).date()
    broker = AlpacaBroker.from_settings(get_settings())
    with SessionLocal() as session:
        monitor(session, broker, today)


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=MARKET_TZ)
    scheduler.add_job(heartbeat, "interval", minutes=5, id="heartbeat")
    # 10:30 ET: an hour after the open, once option quotes have settled.
    scheduler.add_job(
        daily_screener, "cron", day_of_week="mon-fri", hour=10, minute=30, id="daily_screener"
    )
    # From before the open (overnight assignments) to after the close (late fills).
    scheduler.add_job(
        position_monitor,
        "cron",
        day_of_week="mon-fri",
        hour="8-17",
        minute="*/5",
        id="position_monitor",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_scheduler().start()
