"""Scheduled jobs, run in their own process (never inside the API's uvicorn workers).

Jobs are registered here as sprints land: daily screener (~10:30 ET), position monitor
(every 5 min on weekdays: order sync, assignments, exits), then end-of-day snapshots.

    python -m app.worker            # the scheduler (what the compose `worker` service runs)
    python -m app.worker check      # preflight: database, broker, Yahoo, next runs
    python -m app.worker screener   # one screener run now (e.g. stack started after 10:30)
    python -m app.worker monitor    # one monitor pass now
    python -m app.worker tick       # what is due now + queued backtests (hosted cron)
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
from apscheduler.executors.pool import ProcessPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, select, text

from app.broker.alpaca import AlpacaBroker
from app.config import get_settings
from app.db import SessionLocal
from app.domain.params import StrategyParams
from app.marketdata.yahoo import YahooProvider
from app.models.strategy import ScreenerRun
from app.services.lab import fail_interrupted, run_queued_backtests
from app.services.screening import active_config, run_screener
from app.services.trading import monitor

MARKET_TZ = "America/New_York"
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def due_jobs(now: datetime, last_screener_day: date | None) -> list[str]:
    """Jobs a stateless cron tick must run at `now` (market time), same hours as the scheduler.

    The screener is caught up later in the day when the 10:30 tick was missed or delayed, but
    never twice a day.
    """
    if now.weekday() >= 5:
        return []
    jobs = []
    if 8 <= now.hour <= 17:
        jobs.append("monitor")
    if (10, 30) <= (now.hour, now.minute) and now.hour < 16 and last_screener_day != now.date():
        jobs.append("screener")
    return jobs


def tick() -> bool:
    """Run what is due now, then the queued backtests; False if a job failed (the others run).

    Ticks never overlap (one at a time on the host), so a backtest still 'running' when a tick
    starts was cut off by the previous one's timeout.
    """
    now = datetime.now(ZoneInfo(MARKET_TZ))
    with SessionLocal() as session:
        if interrupted := fail_interrupted(session):
            logger.warning("%s backtest(s) interrompu(s) marqué(s) en échec", interrupted)
        last = session.execute(select(func.max(ScreenerRun.started_at))).scalar()
    last_day = last.astimezone(ZoneInfo(MARKET_TZ)).date() if last else None
    jobs = due_jobs(now, last_day)
    logger.info("tick %s: %s", now.isoformat(timespec="minutes"), jobs or "nothing due")
    ok = True
    for job in jobs:
        try:
            {"monitor": position_monitor, "screener": daily_screener}[job]()
        except Exception:
            logger.exception("%s failed", job)
            ok = False
    try:
        run_queued_backtests()
    except Exception:
        logger.exception("backtests failed")
        ok = False
    return ok


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=MARKET_TZ)
    # Backtests are CPU-bound: one at a time, in a child process, so the monitor stays on time.
    scheduler.add_executor(ProcessPoolExecutor(max_workers=1), "backtests")
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
    scheduler.add_job(
        run_queued_backtests,
        "interval",
        seconds=15,
        id="backtests",
        executor="backtests",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def check() -> bool:
    """Print one line per dependency of the trading day; False if any is not ready."""
    settings = get_settings()
    now = datetime.now(ZoneInfo(MARKET_TZ))
    results: list[bool] = []

    def line(name: str, good: bool, detail: str) -> None:
        results.append(good)
        print(f"[{'ok' if good else 'KO'}] {name:<8} {detail}")

    try:
        with SessionLocal() as session:
            current = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        config = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
        config.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
        head = ScriptDirectory.from_config(config).get_current_head()
        line("base", current == head, f"migration {current} (dernière : {head})")
    except Exception as exc:
        line("base", False, f"injoignable ou non migrée : {str(exc)[:200]}")

    try:
        broker = AlpacaBroker.from_settings(settings)
        account = broker._call("GET", "/v2/account")
        clock = broker._call("GET", "/v2/clock")
        level = int(account.get("options_trading_level") or 0)
        line(
            "broker",
            account.get("status") == "ACTIVE" and level >= 3,
            f"{settings.broker} {settings.broker_env}, compte {account.get('status')}, "
            f"niveau options {level}, marché {'ouvert' if clock['is_open'] else 'fermé'}, "
            f"prochaine ouverture {clock['next_open']}",
        )
    except Exception as exc:
        line("broker", False, str(exc)[:200])

    try:
        params = StrategyParams()
        spy = YahooProvider(params.dte_min, params.dte_max).snapshot("SPY", now.date())
        line("yahoo", bool(spy.options), f"SPY {spy.spot:.2f}, {len(spy.options)} options")
    except Exception as exc:
        line("yahoo", False, str(exc)[:200])

    for job in build_scheduler().get_jobs():
        print(f"     {job.id:<17} prochain passage {job.trigger.get_next_fire_time(None, now)}")
    return all(results)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("command", nargs="?", choices=["check", "screener", "monitor", "tick"])
    command = parser.parse_args(argv).command
    logging.basicConfig(level=logging.INFO)
    if command == "check":
        return 0 if check() else 1
    if command == "tick":
        return 0 if tick() else 1
    if command == "screener":
        daily_screener()
    elif command == "monitor":
        position_monitor()
    else:
        with SessionLocal() as session:
            if interrupted := fail_interrupted(session):
                logger.warning("%s backtest(s) interrompu(s) marqué(s) en échec", interrupted)
        build_scheduler().start()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
