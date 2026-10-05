"""Settings lab: named parameter profiles, the active one for the live screener, and backtests.

Backtests are queued by the API and run by the worker in a separate process (a run takes a
minute or two of CPU); the market history they need is cached in the database and fetched
from Yahoo only for missing symbols or on demand.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import Session, defer

from app.backtest.data import HistoryFetcher, MarketHistory
from app.backtest.engine import CALENDAR_SYMBOL, run_backtest
from app.backtest.report import payload
from app.backtest.synth import ModelConfig
from app.domain.params import (
    ETFS,
    PARAM_GROUPS,
    PARAM_SPECS,
    InvalidParams,
    StrategyParams,
    parse_params,
)
from app.models import BacktestRun, MarketHistoryCache, StrategyConfig, StrategyProfile
from app.services.screening import active_config

logger = logging.getLogger(__name__)

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
# The backtest needs a year of history before its first day (IV Rank, moving averages).
WARMUP_DAYS = 400
HISTORY_START = date(2018, 10, 1)
DEFAULT_START = date(2019, 1, 2)
PROGRESS_SECONDS = 2.0

# The first preset is the default one, linked to the active screener version on first use.
PRESETS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "Prudent",
        "Profil par défaut : ETF en put credit spreads et Short Put Income, entrée à 45-65 DTE "
        "(21 jours de détention au moins), delta 0.10-0.20, risque de 1 % du capital par trade, "
        "10 % de perte maximale ouverte, 5 % par cluster, stop mensuel 4 %.",
        {},
    ),
    (
        "Actuel",
        "Anciens réglages par défaut, déficitaires au backtest 2019-2026 (-19,1 %/an, drawdown "
        "86,9 %). Déconseillé : son activation demande une confirmation.",
        {"enable_large_caps": True, "dte_min": 25, "dte_max": 55, "min_holding_days": 0,
         "delta_min": 0.15, "delta_max": 0.30, "use_wheel_max_strike": True,
         "spread_widths": [5.0, 10.0, 2.5], "min_credit": 0.25, "max_trade_risk_pct": 0.10,
         "max_open_risk_pct": 0.50, "max_cluster_risk_pct": 0.50,
         "max_expiration_risk_pct": 0.50, "use_ror_filter": False, "use_aroc_filter": True,
         "min_quality_score": 0.0},
    ),
    (
        "Expérimental : grandes valeurs",
        "Mode expérimental, hors profils standards : put credit spreads sur les grandes valeurs "
        "seules. Déficitaire au backtest 2019-2026 (-11,8 %/an à 40-55 DTE).",
        {"enable_etfs": False, "enable_wheel": False, "enable_true_wheel": False,
         "enable_large_caps": True},
    ),
)  # fmt: skip
# Documented backtests of the presets, until a run of the profile exists.
PRESET_REFERENCES: dict[str, dict[str, Any]] = {
    "Actuel": {
        "cagr": -0.191,
        "max_drawdown": 0.869,
        "profit_factor": 0.74,
        "start": "2019-01-02",
        "end": "2026-10-02",
        "source": "Backtest 2019-2026 des anciens réglages par défaut (docs/backtest-resultats.md)",
    },
    "Expérimental : grandes valeurs": {
        "cagr": -0.118,
        "max_drawdown": 0.784,
        "profit_factor": 0.73,
        "start": "2019-01-02",
        "end": "2026-10-02",
        "source": "Backtest 2019-2026, grandes valeurs seules à 40-55 DTE "
        "(docs/backtest-resultats.md)",
    },
}

# Reconstruction assumptions that can be changed per run, with their labels.
MODEL_SPECS: tuple[tuple[str, str], ...] = (
    ("entry_slippage", "Glissement à l'entrée (part du demi-écart)"),
    ("exit_slippage", "Glissement aux sorties stop et temps (part du demi-écart)"),
    ("index_atm_ratio", "IV ATM SPY/QQQ = VIX/VXN x"),
    ("hv_premium_etf", "IV ATM ETF = volatilité réalisée x"),
    ("hv_premium_stock", "IV ATM actions = volatilité réalisée x"),
    ("skew_etf", "Skew ETF"),
    ("skew_stock", "Skew actions"),
    ("half_spread_etf", "Demi-écart bid/ask ETF (part du prix)"),
    ("half_spread_large_cap", "Demi-écart bid/ask grandes valeurs"),
    ("half_spread_wheel", "Demi-écart bid/ask wheel"),
)


def model_defaults() -> dict[str, float]:
    defaults = ModelConfig()
    return {key: getattr(defaults, key) for key, _ in MODEL_SPECS}


class LabError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


# --- parameters -------------------------------------------------------------------------------


def coerce_params(data: dict[str, Any], base: StrategyParams | None = None) -> StrategyParams:
    try:
        return parse_params(data, base)
    except InvalidParams as exc:
        raise LabError(str(exc)) from None


def param_specs() -> dict[str, Any]:
    return {
        "groups": [{"key": k, "label": label} for k, label in PARAM_GROUPS],
        "fields": [
            {
                "key": s.key,
                "label": s.label,
                "group": s.group,
                "kind": s.kind,
                "help": s.help or None,
                "toggle": s.toggle,
                "min": s.minimum,
                "max": s.maximum,
                "step": s.step,
            }
            for s in PARAM_SPECS
        ],
        "defaults": StrategyParams().to_dict(),
    }


# --- profiles ---------------------------------------------------------------------------------


def ensure_profiles(session: Session) -> None:
    """First use: the active parameters become the default profile, plus the other presets."""
    if session.scalar(select(func.count()).select_from(StrategyProfile)):
        return
    config = active_config(session)
    for index, (name, description, overrides) in enumerate(PRESETS):
        params = (
            StrategyParams.from_dict(config.params)
            if index == 0
            else StrategyParams.from_dict({**StrategyParams().to_dict(), **overrides})
        )
        profile = StrategyProfile(
            name=name,
            description=description,
            params=params.to_dict(),
            updated_at=_now(),
            reference_summary=PRESET_REFERENCES.get(name),
        )
        session.add(profile)
        session.flush()
        if index == 0 and config.profile_id is None:
            config.profile_id = profile.id
    session.flush()


def _profile(session: Session, profile_id: int) -> StrategyProfile:
    profile = session.get(StrategyProfile, profile_id)
    if profile is None:
        raise LabError("Profil introuvable.", 404)
    return profile


def _check_name(session: Session, name: str, profile_id: int | None = None) -> str:
    name = name.strip()
    if not name:
        raise LabError("Le nom du profil est obligatoire.")
    clash = session.scalar(select(StrategyProfile).where(StrategyProfile.name == name))
    if clash is not None and clash.id != profile_id:
        raise LabError(f"Un profil s'appelle déjà « {name} ».", 409)
    return name


def _same_params(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return StrategyParams.from_dict(a).to_dict() == StrategyParams.from_dict(b).to_dict()


def risk_verdict(session: Session, profile: StrategyProfile) -> dict[str, Any]:
    """Backtest record of the profile's exact parameters, and whether activation must warn.

    The latest finished run of the profile with the same parameters is used, else the
    documented reference of a preset. A loss-making record or a drawdown above the profile's
    own drawdown limit makes the activation warning blocking (explicit confirmation needed).
    """
    params = StrategyParams.from_dict(profile.params)
    runs = session.scalars(
        select(BacktestRun)
        .options(defer(BacktestRun.result))
        .where(BacktestRun.profile_id == profile.id, BacktestRun.status == DONE)
        .order_by(BacktestRun.id.desc())
    ).all()
    run = next((r for r in runs if r.summary and _same_params(r.params, profile.params)), None)
    if run is not None:
        record = dict(run.summary)
        source = f"Backtest n° {run.id} ({run.start:%m/%Y} → {run.end:%m/%Y})"
    elif profile.reference_summary:
        record = dict(profile.reference_summary)
        source = record.get("source") or "Backtest de référence"
    else:
        return {
            "status": "untested",
            "blocking": False,
            "source": None,
            "cagr": None,
            "max_drawdown": None,
            "reasons": ["Aucun backtest terminé avec ces réglages exacts : lance-en un."],
        }
    cagr, drawdown = record.get("cagr"), record.get("max_drawdown")
    reasons = []
    if cagr is not None and cagr < 0:
        reasons.append(f"Historiquement déficitaire : {cagr:.1%} par an.")
    if drawdown is not None and drawdown > params.max_drawdown_pct:
        reasons.append(
            f"Drawdown de {drawdown:.1%}, au-delà de la limite de {params.max_drawdown_pct:.0%}."
        )
    status = "ok"
    if cagr is not None and cagr < 0:
        status = "deficit"
    elif reasons:
        status = "drawdown"
    return {
        "status": status,
        "blocking": bool(reasons),
        "source": source,
        "cagr": cagr,
        "max_drawdown": drawdown,
        "reasons": reasons,
    }


def profile_view(
    profile: StrategyProfile, active_id: int | None, verdict: dict[str, Any] | None = None
) -> dict[str, Any]:
    params = StrategyParams.from_dict(profile.params)
    defaults = StrategyParams().to_dict()
    current = params.to_dict()
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "params": current,
        # Keys that differ from the defaults, for a short summary on the list.
        "changed": [k for k, v in current.items() if defaults.get(k) != v],
        "is_active": profile.id == active_id,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat(),
        "risk": verdict,
    }


def list_profiles(session: Session) -> dict[str, Any]:
    ensure_profiles(session)
    config = active_config(session)
    profiles = session.scalars(select(StrategyProfile).order_by(StrategyProfile.id)).all()
    return {
        "profiles": [
            profile_view(p, config.profile_id, risk_verdict(session, p)) for p in profiles
        ],
        "active_profile_id": config.profile_id,
        "active_version": config.version,
        **param_specs(),
    }


def create_profile(
    session: Session,
    name: str,
    description: str | None,
    params: dict[str, Any],
    copy_from: int | None = None,
) -> StrategyProfile:
    ensure_profiles(session)
    base = StrategyParams.from_dict(_profile(session, copy_from).params) if copy_from else None
    profile = StrategyProfile(
        name=_check_name(session, name),
        description=(description or "").strip() or None,
        params=coerce_params(params, base).to_dict(),
        updated_at=_now(),
    )
    session.add(profile)
    session.flush()
    return profile


def update_profile(
    session: Session,
    profile_id: int,
    name: str | None = None,
    description: str | None = None,
    params: dict[str, Any] | None = None,
    confirm_risk: bool = False,
) -> StrategyProfile:
    """Save a profile; saving the active one sends its new parameters to the live screener."""
    profile = _profile(session, profile_id)
    if name is not None:
        profile.name = _check_name(session, name, profile.id)
    if description is not None:
        profile.description = description.strip() or None
    if params is not None:
        new = coerce_params(params, StrategyParams.from_dict(profile.params)).to_dict()
        if not _same_params(new, profile.params):
            # The documented backtest no longer describes these parameters.
            profile.reference_summary = None
        profile.params = new
    profile.updated_at = _now()
    session.flush()
    if active_config(session).profile_id == profile.id:
        activate_profile(session, profile.id, confirm_risk)
    return profile


def activate_profile(
    session: Session, profile_id: int, confirm_risk: bool = False
) -> StrategyConfig:
    """Write the profile as a new active strategy_configs version (unless already identical).

    A profile whose backtest lost money or broke its drawdown limit is refused unless
    `confirm_risk` is set; the warnings shown are kept with the version. Open positions keep
    the exit rules of the version they were opened with.
    """
    profile = _profile(session, profile_id)
    current = active_config(session)
    params = StrategyParams.from_dict(profile.params).to_dict()
    if current.profile_id == profile.id and StrategyParams.from_dict(current.params).to_dict() == (
        params
    ):
        return current
    verdict = risk_verdict(session, profile)
    if verdict["blocking"] and not confirm_risk:
        raise LabError(
            "Activation bloquée : " + " ".join(verdict["reasons"]) + " Confirme explicitement "
            "pour l'activer quand même.",
            409,
        )
    session.execute(update(StrategyConfig).where(StrategyConfig.is_active).values(is_active=False))
    version = (session.scalar(select(func.max(StrategyConfig.version))) or 0) + 1
    config = StrategyConfig(
        version=version,
        params=params,
        is_active=True,
        profile_id=profile.id,
        activation={
            "at": _now().isoformat(),
            "risk_status": verdict["status"],
            "warnings": verdict["reasons"] if verdict["blocking"] else [],
            "confirmed": bool(verdict["blocking"] and confirm_risk),
        },
    )
    session.add(config)
    session.flush()
    return config


def delete_profile(session: Session, profile_id: int) -> None:
    profile = _profile(session, profile_id)
    if active_config(session).profile_id == profile.id:
        raise LabError("Le profil actif ne peut pas être supprimé : active d'abord un autre.", 409)
    session.delete(profile)
    session.flush()


# --- market history cache ---------------------------------------------------------------------


def cache_row(session: Session) -> MarketHistoryCache | None:
    return session.scalar(select(MarketHistoryCache).order_by(MarketHistoryCache.id.desc()))


def cache_view(session: Session) -> dict[str, Any] | None:
    row = session.execute(
        select(
            MarketHistoryCache.fetched_at,
            MarketHistoryCache.first_day,
            MarketHistoryCache.last_day,
            MarketHistoryCache.symbols,
            MarketHistoryCache.size,
        ).order_by(MarketHistoryCache.id.desc())
    ).first()
    if row is None:
        return None
    return {
        "fetched_at": row.fetched_at.isoformat(),
        "first_day": row.first_day.isoformat(),
        "last_day": row.last_day.isoformat(),
        "symbols": row.symbols,
        "size": row.size,
    }


def store_history(session: Session, market: MarketHistory) -> MarketHistoryCache:
    """Replace the cached history (one row is kept)."""
    span = market.span()
    if span is None:
        raise LabError("Historique vide.")
    data = market.to_bytes()
    row = MarketHistoryCache(
        fetched_at=_now(),
        first_day=span[0],
        last_day=span[1],
        symbols=sorted(market.symbols),
        data=data,
        size=len(data),
    )
    session.execute(delete(MarketHistoryCache))
    session.add(row)
    session.flush()
    return row


Fetch = Callable[[list[str], list[str], date, date], MarketHistory]


def yahoo_fetch(
    symbols: list[str], etfs: list[str], start: date, end: date
) -> MarketHistory:  # pragma: no cover - network
    return HistoryFetcher(start, end).fetch(symbols, etfs)


def load_history(
    session: Session,
    symbols: list[str],
    etfs: list[str],
    refresh: bool,
    fetch: Fetch,
    today: date,
) -> MarketHistory:
    """The cached history, completed with missing symbols (or fully refetched on `refresh`)."""
    row = cache_row(session)
    market = MarketHistory.from_bytes(row.data) if row is not None else None
    needed = list(dict.fromkeys([CALENDAR_SYMBOL, *symbols]))
    if market is None or refresh:
        wanted = sorted(set(needed) | (set(market.symbols) if market else set()))
        fresh = fetch(wanted, sorted(set(etfs) | set(ETFS)), HISTORY_START, today)
        market = market.merged(fresh) if market else fresh
        store_history(session, market)
        return market
    missing = [s for s in needed if s not in market.symbols]
    if missing:
        span = market.span()
        fresh = fetch(missing, sorted(set(etfs) | set(ETFS)), HISTORY_START, span[1])
        market = market.merged(fresh)
        store_history(session, market)
    return market


# --- backtest runs ----------------------------------------------------------------------------


def coerce_model(data: dict[str, Any] | None) -> dict[str, float]:
    allowed = {key for key, _ in MODEL_SPECS}
    out: dict[str, float] = {}
    for key, value in (data or {}).items():
        if key not in allowed:
            raise LabError(f"Hypothèse inconnue : {key}.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise LabError(f"Hypothèse {key} : valeur invalide.") from None
        if not 0 <= number <= 10:
            raise LabError(f"Hypothèse {key} : entre 0 et 10.")
        out[key] = number
    return out


def queue_backtest(
    session: Session,
    profile_id: int | None,
    params: dict[str, Any] | None,
    start: date,
    end: date,
    capital: float,
    model: dict[str, Any] | None = None,
    refresh_data: bool = False,
    name: str | None = None,
) -> BacktestRun:
    """Queue a run of a saved profile, or of unsaved parameters (a draft from the form)."""
    if start >= end:
        raise LabError("La date de début doit précéder la date de fin.")
    if start < HISTORY_START + timedelta(days=90):
        raise LabError(
            f"L'historique commence en {HISTORY_START:%m/%Y} : début au plus tôt en 2019."
        )
    if capital <= 0:
        raise LabError("Le capital doit être positif.")
    profile = _profile(session, profile_id) if profile_id is not None else None
    base = StrategyParams.from_dict(profile.params) if profile else None
    resolved = coerce_params(params or {}, base)
    run = BacktestRun(
        profile_id=profile.id if profile else None,
        profile_name=(name or (profile.name if profile else "Brouillon")).strip()[:80],
        params=resolved.to_dict(),
        model=coerce_model(model),
        start=start,
        end=end,
        capital=capital,
        refresh_data=refresh_data,
        status=QUEUED,
        progress=0.0,
        step="En attente du worker",
    )
    session.add(run)
    session.flush()
    return run


def run_view(run: BacktestRun, details: bool = False) -> dict[str, Any]:
    out = {
        "id": run.id,
        "profile_id": run.profile_id,
        "profile_name": run.profile_name,
        "status": run.status,
        "progress": run.progress,
        "step": run.step,
        "error": run.error,
        "start": run.start.isoformat(),
        "end": run.end.isoformat(),
        "capital": run.capital,
        "model": run.model,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "summary": run.summary,
    }
    if details:
        out["params"] = StrategyParams.from_dict(run.params).to_dict()
        out["result"] = run.result
    return out


def list_runs(session: Session, limit: int = 100) -> list[dict[str, Any]]:
    # The details (trades, curve) stay in the database: up to ~600 kB per run.
    query = select(BacktestRun).options(defer(BacktestRun.result))
    runs = session.scalars(query.order_by(BacktestRun.id.desc()).limit(limit)).all()
    out = []
    for r in runs:
        view = run_view(r)
        view["params"] = StrategyParams.from_dict(r.params).to_dict()
        out.append(view)
    return out


def get_run(session: Session, run_id: int) -> BacktestRun:
    run = session.get(BacktestRun, run_id)
    if run is None:
        raise LabError("Backtest introuvable.", 404)
    return run


def delete_run(session: Session, run_id: int) -> None:
    run = get_run(session, run_id)
    if run.status == RUNNING:
        raise LabError("Ce backtest est en cours : attends la fin pour le supprimer.", 409)
    session.delete(run)
    session.flush()


def claim_next(session: Session) -> BacktestRun | None:
    """Oldest queued run, marked running; SKIP LOCKED keeps two workers off the same run."""
    run = session.scalar(
        select(BacktestRun)
        .where(BacktestRun.status == QUEUED)
        .order_by(BacktestRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if run is None:
        return None
    run.status = RUNNING
    run.started_at = _now()
    run.step = "Démarrage"
    session.commit()
    return run


def fail_interrupted(session: Session) -> int:
    """Runs left 'running' by a stopped worker are marked failed at its next start."""
    result = session.execute(
        update(BacktestRun)
        .where(BacktestRun.status == RUNNING)
        .values(status=FAILED, error="Interrompu par un arrêt du worker.", finished_at=_now())
    )
    session.commit()
    return result.rowcount or 0


def execute_run(
    session: Session, run: BacktestRun, fetch: Fetch = yahoo_fetch, today: date | None = None
) -> BacktestRun:
    """Run one claimed backtest to the end, committing progress as it goes."""
    today = today or date.today()
    params = StrategyParams.from_dict(run.params)
    try:
        run.step = "Chargement de l'historique de marché"
        session.commit()
        market = load_history(
            session, list(params.universe), list(params.etfs), run.refresh_data, fetch, today
        )
        span = market.span()
        end = min(run.end, span[1])
        if end <= run.start:
            raise LabError(f"L'historique en cache s'arrête au {span[1]:%d/%m/%Y}.")
        run.step = "Simulation"
        session.commit()

        last = [0.0]

        def progress(share: float) -> None:
            now = time.monotonic()
            if now - last[0] >= PROGRESS_SECONDS or share >= 1:
                last[0] = now
                run.progress = round(share, 3)
                session.commit()

        known = {f.name for f in fields(ModelConfig)}
        model = ModelConfig(**{k: v for k, v in run.model.items() if k in known})
        result = run_backtest(market, params, run.start, end, run.capital, model, progress)
        if not result.equity:
            raise LabError("Aucun jour de bourse dans la période choisie.")
        spy = market.symbols[CALENDAR_SYMBOL]
        summary, details = payload(result, list(zip(spy.dates, spy.closes, strict=True)))
        if end < run.end:
            summary["note"] = f"Historique en cache jusqu'au {end:%d/%m/%Y}."
        run.summary, run.result = summary, details
        run.status, run.progress, run.step = DONE, 1.0, None
    except Exception as exc:  # the run is marked failed, the worker keeps going
        logger.exception("backtest %s failed", run.id)
        session.rollback()
        run = session.get(BacktestRun, run.id)
        run.status, run.step = FAILED, None
        run.error = str(exc)[:2000] or exc.__class__.__name__
    run.finished_at = _now()
    session.commit()
    return run


def run_queued_backtests() -> None:  # pragma: no cover - worker process
    """Worker job, in its own process: run every queued backtest, oldest first."""
    from app.config import get_settings

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        with Session(engine, expire_on_commit=False) as session:
            while (run := claim_next(session)) is not None:
                logger.info("backtest %s: %s", run.id, run.profile_name)
                execute_run(session, run)
    finally:
        engine.dispose()
