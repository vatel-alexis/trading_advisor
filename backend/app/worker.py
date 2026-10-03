"""Scheduled jobs, run in their own process (never inside the API's uvicorn workers).

Jobs are registered here as sprints land: daily screener (~10:30 ET), position monitor
(every 1-5 min during market hours), end-of-day snapshots and broker reconciliation.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

MARKET_TZ = "America/New_York"

logger = logging.getLogger("worker")


def heartbeat() -> None:
    logger.info("worker alive")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=MARKET_TZ)
    scheduler.add_job(heartbeat, "interval", minutes=5, id="heartbeat")
    return scheduler


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_scheduler().start()
