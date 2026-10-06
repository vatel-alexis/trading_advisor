from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from app.models import PeaReport
from app.services import pea
from app.worker import pea_due

PARIS = ZoneInfo("Europe/Paris")


def payload(as_of: str, signal_day: str, changes: bool, preview: bool) -> dict:
    move = [{"asset": "japan", "from": 0.0, "to": 0.5, "action": "acheter"}]
    return {
        "as_of": as_of,
        "signal_day": signal_day,
        "provisional_day": as_of,
        "levels": [
            {"key": "moyen", "label": "Moyen", "changes": move if changes else [],
             "preview_changes": move if preview else []},
            {"key": "dynamique", "label": "Dynamique", "changes": [], "preview_changes": []},
        ],
    }  # fmt: skip


def test_store_keeps_the_newest_report_and_drops_old_ones(session: Session) -> None:
    pea.store(session, payload("2026-08-01", "2026-07-31", False, False))
    pea.store(session, payload("2026-10-05", "2026-09-30", True, False))
    newest = pea.store(session, payload("2026-10-06", "2026-09-30", True, True))

    assert pea.latest(session).id == newest.id
    # The August report is more than 45 days older than the newest one.
    assert session.query(PeaReport).count() == 2
    assert pea.view(session)["report"]["as_of"] == "2026-10-06"


def test_alert_shows_new_allocations_for_a_few_days(session: Session) -> None:
    assert pea.alert(session, date(2026, 10, 6))["active"] is False  # no report yet
    pea.store(session, payload("2026-10-06", "2026-09-30", True, True))

    fresh = pea.alert(session, date(2026, 10, 6))
    assert fresh["active"] and fresh["levels"] == ["Moyen"]
    assert fresh["provisional"] == ["Moyen"]
    late = pea.alert(session, date(2026, 10, 20))
    assert not late["active"] and late["levels"] == []


def test_api_serves_the_report_and_the_alert(session: Session) -> None:
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        empty = client.get("/pea")
        pea.store(session, payload("2026-10-06", "2026-09-30", True, False))
        full = client.get("/pea")
        banner = client.get("/pea/alert")
    finally:
        app.dependency_overrides.clear()

    assert empty.status_code == 200 and empty.json()["report"] is None
    assert full.json()["report"]["signal_day"] == "2026-09-30"
    assert banner.status_code == 200 and "levels" in banner.json()


def paris(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=PARIS)


@pytest.mark.parametrize(
    ("now", "last", "expected"),
    [
        (paris(6, 10), None, True),  # no report yet: right away
        (paris(6, 17, 55), paris(5, 18, 5), False),  # Paris still open
        (paris(6, 18, 0), paris(5, 18, 5), True),
        (paris(6, 18, 30), paris(6, 18, 5), False),  # already done today
        (paris(6, 18, 30), paris(6, 9, 0), True),  # today's was before the close
        (paris(10, 19), paris(9, 18, 5), True),  # Saturday: a new month may have begun
    ],
)
def test_pea_due(now: datetime, last: datetime | None, expected: bool) -> None:
    assert pea_due(now.astimezone(UTC), last) == expected
