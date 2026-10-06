"""Scheduled jobs, run in their own process (never inside the API's uvicorn workers).

Jobs are registered here as sprints land: daily screener (~10:30 ET), position monitor
(every 5 min on weekdays: order sync, assignments, exits), then end-of-day snapshots.

    python -m app.worker            # the scheduler (what the compose `worker` service runs)
    python -m app.worker check      # preflight: database, broker, Yahoo, next runs
    python -m app.worker screener   # one screener run now (e.g. stack started after 10:30)
    python -m app.worker monitor    # one monitor pass now
    python -m app.worker pea        # recompute the PEA ETF portfolio page now
    python -m app.worker tick       # what is due now + queued backtests (hosted cron)
    python -m app.worker loop       # ticks for a few hours (hosted worker, see worker.yml)
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
from apscheduler.executors.pool import ProcessPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, select, text

from app.broker.alpaca import AlpacaBroker
from app.config import get_settings
from app.db import SessionLocal, engine
from app.domain.params import StrategyParams
from app.marketdata.yahoo import YahooClient, YahooProvider
from app.models.strategy import ScreenerRun
from app.services import pea
from app.services.lab import fail_interrupted, run_queued_backtests
from app.services.safety import MONITOR, SCREENER, TICK, record_job
from app.services.screening import active_config, run_screener
from app.services.trading import monitor

MARKET_TZ = "America/New_York"
PARIS_TZ = ZoneInfo("Europe/Paris")
PEA = "pea"
# Prices of the day are final a little after the 17:35 close.
PARIS_CLOSE = dt_time(18, 0)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("worker")


def _record_failure(job: str, exc: Exception) -> None:
    """A job that crashed before recording its own outcome: the entry gate must see it."""
    try:
        with SessionLocal() as session:
            record_job(session, job, ok=False, error=f"{exc.__class__.__name__}: {exc}")
            session.commit()
    except Exception:
        logger.exception("could not record the failure of %s", job)


def heartbeat() -> None:
    logger.info("worker alive")
    with SessionLocal() as session:
        record_job(session, TICK, ok=True)
        session.commit()


def daily_screener() -> None:
    today = datetime.now(ZoneInfo(MARKET_TZ)).date()
    try:
        with SessionLocal() as session:
            params = StrategyParams.from_dict(active_config(session).params)
            provider = YahooProvider(params.dte_min, params.dte_max)
            run = run_screener(session, provider, today, get_settings().starting_capital)
            session.commit()
    except Exception as exc:
        _record_failure(SCREENER, exc)
        raise
    logger.info("screener run %s: %s", run.id, run.filter_counts)


def position_monitor() -> None:
    today = datetime.now(ZoneInfo(MARKET_TZ)).date()
    try:
        settings = get_settings()
        broker = AlpacaBroker.from_settings(settings)
        with SessionLocal() as session:
            monitor(session, broker, today, settings.starting_capital)
    except Exception as exc:
        _record_failure(MONITOR, exc)
        raise


def pea_refresh() -> None:
    """Recompute the PEA page from Yahoo's daily prices (the ETFs trade in Paris)."""
    today = datetime.now(PARIS_TZ).date()
    try:
        prices, fx = pea.fetch_prices(YahooClient())
        payload = pea.build_report(prices, fx, today)
        with SessionLocal() as session:
            pea.store(session, payload)
            record_job(session, PEA, ok=True)
            session.commit()
    except Exception as exc:
        _record_failure(PEA, exc)
        raise
    logger.info("PEA report as of %s, signal %s", payload["as_of"], payload["signal_day"])


def pea_due(now: datetime, last: datetime | None) -> bool:
    """Right away when no report exists, then once a day after the Euronext close (17:35,
    Paris time); a report computed earlier the same day, before the close, is redone. Weekends
    included: the month-end signal is confirmed on the first day of the new month, often a
    Saturday or Sunday, in time to trade at the next open."""
    if last is None:
        return True
    now, last = now.astimezone(PARIS_TZ), last.astimezone(PARIS_TZ)
    after_close = now.time() >= PARIS_CLOSE
    done = last.date() == now.date() and last.time() >= PARIS_CLOSE
    return after_close and not done


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
        last_pea = pea.last_computed(session)
        record_job(session, TICK, ok=True)
        session.commit()
    last_day = last.astimezone(ZoneInfo(MARKET_TZ)).date() if last else None
    jobs = due_jobs(now, last_day)
    if pea_due(now, last_pea):
        jobs.append(PEA)
    logger.info("tick %s: %s", now.isoformat(timespec="minutes"), jobs or "nothing due")
    ok = True
    for job in jobs:
        try:
            {"monitor": position_monitor, "screener": daily_screener, PEA: pea_refresh}[job]()
        except Exception:
            logger.exception("%s failed", job)
            ok = False
    try:
        run_queued_backtests()
    except Exception:
        logger.exception("backtests failed")
        ok = False
    return ok


def next_tick_delay(now: datetime) -> float:
    """Seconds until the next tick: every 5 minutes while the monitor is due, else at the top
    of the next hour (only queued backtests are left to run, and Neon can sleep in between)."""
    if now.weekday() < 5 and 8 <= now.hour <= 17:
        return 300.0
    top = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (top - now).total_seconds()


def loop(
    budget: timedelta,
    clock: Callable[[], datetime] = lambda: datetime.now(ZoneInfo(MARKET_TZ)),
    sleep: Callable[[float], None] = time.sleep,
    run: Callable[[], bool] = tick,
) -> int:
    """Tick on the worker's own clock until `budget` is spent; returns the number of ticks.

    GitHub skips most scheduled runs, so the hosted worker cannot count on its cron: one run
    loops for a few hours, then the workflow starts the next one. A failed tick is recorded by
    the jobs themselves (the entry gate sees it) and the loop carries on.
    """
    start = clock()
    ticks = 0
    while True:
        try:
            run()
        except Exception:
            logger.exception("tick failed")
        ticks += 1
        # No idle connection between ticks, so Neon scales to zero.
        engine.dispose()
        now = clock()
        delay = next_tick_delay(now)
        if now + timedelta(seconds=delay) - start > budget:
            return ticks
        sleep(delay)


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
    parser.add_argument(
        "command", nargs="?", choices=["check", "screener", "monitor", "tick", "loop", "pea"]
    )
    parser.add_argument("--minutes", type=int, default=330, help="loop: durée en minutes")
    args = parser.parse_args(argv)
    command = args.command
    logging.basicConfig(level=logging.INFO)
    if command == "check":
        return 0 if check() else 1
    if command == "tick":
        return 0 if tick() else 1
    if command == "loop":
        logger.info("%s tick(s)", loop(timedelta(minutes=args.minutes)))
        return 0
    if command == "screener":
        daily_screener()
    elif command == "monitor":
        position_monitor()
    elif command == "pea":
        pea_refresh()
    else:
        with SessionLocal() as session:
            if interrupted := fail_interrupted(session):
                logger.warning("%s backtest(s) interrompu(s) marqué(s) en échec", interrupted)
        build_scheduler().start()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
