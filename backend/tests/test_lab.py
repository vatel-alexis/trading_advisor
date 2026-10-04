"""Settings profiles, the active version for the live screener, and backtests from the API."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.domain.params import StrategyParams
from app.main import app
from app.models import BacktestRun, StrategyConfig, StrategyProfile
from app.services import lab
from app.services.screening import active_config
from tests.test_backtest import market, wavy

START, END = date(2019, 1, 2), date(2019, 11, 15)


def fake_fetch(calls: list):
    """A Yahoo stand-in: SPY only, 2018-2019, recorded calls."""

    def fetch(symbols, etfs, start, end):
        calls.append(sorted(symbols))
        return market(wavy(500))

    return fetch


def test_first_use_turns_the_active_parameters_into_profiles(session: Session) -> None:
    data = lab.list_profiles(session)

    names = [p["name"] for p in data["profiles"]]
    assert names == ["Actuel", "Prudent"]
    actuel, prudent = data["profiles"]
    assert actuel["is_active"] and data["active_profile_id"] == actuel["id"]
    assert actuel["changed"] == []
    assert set(prudent["changed"]) == {
        "enable_large_caps",
        "dte_min",
        "dte_max",
        "delta_min",
        "delta_max",
    }
    assert {f["key"] for f in data["fields"]} >= {"use_trend_filter", "min_iv_rank"}
    lab.list_profiles(session)  # idempotent
    assert len(session.scalars(select(StrategyProfile)).all()) == 2


def test_activating_a_profile_writes_a_new_screener_version(session: Session) -> None:
    lab.ensure_profiles(session)
    before = active_config(session)
    prudent = session.scalar(select(StrategyProfile).where(StrategyProfile.name == "Prudent"))

    config = lab.activate_profile(session, prudent.id)

    assert config.version == before.version + 1 and config.profile_id == prudent.id
    assert not before.is_active and active_config(session).id == config.id
    assert StrategyParams.from_dict(config.params).dte_min == 40
    # Same parameters again: no new version.
    assert lab.activate_profile(session, prudent.id).id == config.id
    # Saving the active profile sends the change to the screener.
    lab.update_profile(session, prudent.id, params={"min_iv_rank": 40})
    assert StrategyParams.from_dict(active_config(session).params).min_iv_rank == 40
    assert active_config(session).version == config.version + 1
    with pytest.raises(lab.LabError, match="profil actif"):
        lab.delete_profile(session, prudent.id)


def test_profile_names_are_unique_and_params_validated(session: Session) -> None:
    lab.ensure_profiles(session)
    with pytest.raises(lab.LabError, match="déjà"):
        lab.create_profile(session, "Prudent", None, {})
    with pytest.raises(lab.LabError, match="DTE min"):
        lab.create_profile(session, "Mauvais", None, {"dte_min": 90})
    copy = lab.create_profile(session, "Copie", "test", {"max_deals": 2}, copy_from=None)
    assert StrategyParams.from_dict(copy.params).max_deals == 2


def test_a_queued_backtest_runs_and_stores_its_results(session: Session) -> None:
    lab.ensure_profiles(session)
    actuel = session.scalar(select(StrategyProfile).where(StrategyProfile.name == "Actuel"))
    run = lab.queue_backtest(
        session,
        actuel.id,
        {"enable_large_caps": False, "enable_wheel": False, "etfs": ["SPY"]},
        START,
        END,
        20_000,
        {"entry_slippage": 0.5},
    )
    assert run.status == lab.QUEUED and run.profile_name == "Actuel"

    claimed = lab.claim_next(session)
    assert claimed.id == run.id and claimed.status == lab.RUNNING
    calls: list = []
    done = lab.execute_run(session, claimed, fake_fetch(calls), today=date(2019, 11, 29))

    assert done.status == lab.DONE, done.error
    assert calls == [["SPY"]]  # nothing cached yet: one download
    assert done.summary["trades"] > 0 and done.summary["benchmark_final"] > 0
    assert done.result["equity"][0]["benchmark"] == 20_000
    assert done.result["model"]["entry_slippage"] == 0.5
    assert lab.cache_view(session)["symbols"] == ["SPY"]

    # The second run reads the cache.
    again = lab.queue_backtest(
        session,
        actuel.id,
        {"etfs": ["SPY"], "enable_large_caps": False, "enable_wheel": False},
        START,
        END,
        10_000,
    )
    lab.execute_run(session, lab.claim_next(session), fake_fetch(calls), today=date(2019, 11, 29))
    assert again.status == lab.DONE and len(calls) == 1


def test_a_failing_backtest_is_marked_failed(session: Session) -> None:
    lab.ensure_profiles(session)

    def broken(*_):
        raise RuntimeError("Yahoo injoignable")

    lab.queue_backtest(session, None, {"etfs": ["SPY"]}, START, END, 20_000)
    done = lab.execute_run(session, lab.claim_next(session), broken)
    assert done.status == lab.FAILED and "Yahoo" in done.error


def test_interrupted_runs_fail_at_worker_start(session: Session) -> None:
    run = lab.queue_backtest(session, None, {}, START, END, 20_000)
    lab.claim_next(session)
    assert lab.fail_interrupted(session) == 1
    session.refresh(run)
    assert run.status == lab.FAILED


def test_api_profiles_and_backtests(session: Session) -> None:
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        listing = client.get("/profiles").json()
        created = client.post(
            "/profiles",
            json={"name": "Tendance", "params": {"use_trend_filter": True}, "copy_from": 2},
        )
        bad = client.post("/profiles", json={"name": "X", "params": {"delta_min": "abc"}})
        pid = created.json()["id"]
        renamed = client.put(f"/profiles/{pid}", json={"name": "Tendance 200"})
        activated = client.post(f"/profiles/{pid}/activate")
        queued = client.post(
            "/backtests", json={"profile_id": pid, "start": "2019-01-02", "end": "2019-11-15"}
        )
        empty = client.post("/backtests", json={})
        runs = client.get("/backtests").json()
        detail = client.get(f"/backtests/{queued.json()['id']}")
        removed = client.delete(f"/backtests/{queued.json()['id']}")
        missing = client.get("/backtests/999999")
    finally:
        app.dependency_overrides.clear()

    assert len(listing["profiles"]) == 2
    assert created.status_code == 200, created.text
    assert created.json()["params"]["dte_min"] == 40  # copied from Prudent
    assert created.json()["params"]["use_trend_filter"] is True
    assert bad.status_code == 422 and "invalide" in bad.json()["detail"]
    assert renamed.json()["name"] == "Tendance 200"
    assert activated.status_code == 200
    assert session.scalar(select(StrategyConfig).where(StrategyConfig.is_active)).profile_id == pid
    assert queued.status_code == 200 and queued.json()["status"] == "queued"
    assert empty.status_code == 422
    assert runs["runs"][0]["profile_name"] == "Tendance 200" and runs["cache"] is None
    assert "entry_slippage" in runs["model_defaults"]
    assert detail.json()["params"]["use_trend_filter"] is True
    assert removed.status_code == 200 and missing.status_code == 404
    assert session.get(BacktestRun, queued.json()["id"]) is None
