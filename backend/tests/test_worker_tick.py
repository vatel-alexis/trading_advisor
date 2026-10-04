from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.worker import due_jobs

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
