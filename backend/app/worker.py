"""Scheduled jobs, run in their own process (never inside the API's uvicorn workers).

Jobs are registered here as sprints land: daily screener (~10:30 ET), position monitor
(every 1-5 min during market hours), end-of-day snapshots and broker reconciliation.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.db import SessionLocal
from app.domain.params import StrategyParams
from app.marketdata.yahoo import YahooProvider
from app.services.screening import active_config, run_screener

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


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=MARKET_TZ)
    scheduler.add_job(heartbeat, "interval", minutes=5, id="heartbeat")
    # 10:30 ET: an hour after the open, once option quotes have settled.
    scheduler.add_job(
        daily_screener, "cron", day_of_week="mon-fri", hour=10, minute=30, id="daily_screener"
    )
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_scheduler().start()
