from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.worker import due_jobs, loop, next_tick_delay

NY = ZoneInfo("America/New_York")
MONDAY = date(2026, 10, 5)


def at(hour: int, minute: int, day: date = MONDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)


@pytest.mark.parametrize(
    ("now", "last", "expected"),
    [
        (at(7, 55), None, []),
        (at(8, 0), None, ["monitor"]),
        (at(10, 25), None, ["monitor"]),
        (at(10, 30), date(2026, 10, 2), ["monitor", "screener"]),
        # A delayed or missed 10:30 tick is caught up, once.
        (at(13, 10), None, ["monitor", "screener"]),
        (at(13, 15), MONDAY, ["monitor"]),
        (at(16, 5), None, ["monitor"]),
        (at(17, 55), MONDAY, ["monitor"]),
        (at(18, 0), MONDAY, []),
        (at(11, 0, date(2026, 10, 10)), None, []),  # Saturday
    ],
)
def test_due_jobs(now: datetime, last: date | None, expected: list[str]) -> None:
    assert due_jobs(now, last) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(9, 12), 300.0),
        (at(17, 58), 300.0),
        (at(18, 0), 3600.0),
        (at(7, 15), 2700.0),  # wakes at 08:00 for the first monitor pass
        (at(11, 40, date(2026, 10, 10)), 1200.0),  # Saturday: backtests only
    ],
)
def test_next_tick_delay(now: datetime, expected: float) -> None:
    assert next_tick_delay(now) == expected


def test_loop_stops_within_its_budget() -> None:
    clock = [at(9, 0)]
    ticks: list[datetime] = []

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    def run() -> bool:
        ticks.append(clock[0])
        if len(ticks) == 2:
            raise RuntimeError("a crashed tick does not end the loop")
        return True

    count = loop(timedelta(minutes=60), clock=lambda: clock[0], sleep=sleep, run=run)
    assert count == len(ticks) == 13
    assert ticks[0] == at(9, 0) and ticks[-1] == at(10, 0)
