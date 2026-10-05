"""Strategy and risk parameters. Stored as JSON in strategy_configs, one version per change.

Every filter and exit rule has an on/off switch (`use_*`), so a settings profile can include or
exclude it without losing its threshold. The defaults are the "Prudent" profile (ETFs and the
wheel, 40-55 DTE, delta 0.10-0.20) with conservative risk limits: the former defaults lost
19 %/year in the 2019-2026 backtest. The optional indicators (trend, IV/HV) start switched off.
"""

from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

ETFS = ("SPY", "QQQ", "IWM")
LARGE_CAPS = (
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "JPM", "BAC", "XOM",
    "KO", "PFE", "T", "INTC",
)  # fmt: skip
# Short Put Income names: puts sold and bought back before expiration (21 DTE), never meant to
# be assigned. Their size comes from the stress loss, not from a price cap.
WHEEL = (
    "F", "SOFI", "AAL", "RIVN", "NCLH", "VALE", "ITUB", "HBAN", "KEY", "PCG",
    "CLF", "AGNC", "LYFT", "SNAP", "NIO", "RIG",
)  # fmt: skip


# Strategy groups (Candidate.group): a strategy each, kept apart in sizing and reports.
ETF_GROUP = "etf"  # put credit spreads on index ETFs
LARGE_CAP_GROUP = "large_cap"  # put credit spreads on large caps (experimental)
SHORT_PUT_GROUP = "short_put"  # Short Put Income: sold puts, exit at 21 DTE
TRUE_WHEEL_GROUP = "true_wheel"  # puts on stocks the user accepts to own, then covered calls


@dataclass(frozen=True)
class StrategyParams:
    # Universe. Standard strategies: put credit spreads on index ETFs, and Short Put Income
    # (`enable_wheel` / `wheel`, the keys kept from when it was called the wheel).
    enable_etfs: bool = True
    # Experimental: large caps lost money in every backtest variant, off in standard profiles.
    enable_large_caps: bool = False
    enable_wheel: bool = True
    etfs: tuple[str, ...] = ETFS
    large_caps: tuple[str, ...] = LARGE_CAPS
    wheel: tuple[str, ...] = WHEEL
    # True Wheel: assignment accepted, shares kept and covered calls sold. Only on stocks the
    # user explicitly accepts to own (empty by default).
    enable_true_wheel: bool = True
    true_wheel: tuple[str, ...] = ()
    # Former main criterion of the wheel, now an optional filter: the risk budget sizes puts.
    use_wheel_max_strike: bool = False
    wheel_max_strike: float = 20.0

    # Contract filters. Entries far enough from the 21 DTE exit to leave time to the trade.
    dte_min: int = 45
    dte_max: int = 65
    # holding window = entry DTE - exit DTE (the whole DTE when there is no time exit).
    min_holding_days: int = 21
    # Indicative band of the sold leg's absolute delta (not a probability of profit).
    delta_min: float = 0.10
    delta_max: float = 0.20
    use_open_interest_filter: bool = True
    min_open_interest: int = 100
    use_volume_filter: bool = True
    min_volume: int = 10
    use_spread_filter: bool = True
    max_spread_pct: float = 0.15
    use_iv_rank_filter: bool = True
    min_iv_rank: float = 30.0
    # Return on risk over the whole trade, not annualized (annualizing favours short DTEs).
    # Spreads: credit / max loss. Single puts: credit / cash held.
    use_ror_filter: bool = True
    min_ror_spread: float = 0.10
    min_ror_put: float = 0.01
    # Annualized return (AROC), kept for information; off by default.
    use_aroc_filter: bool = False
    min_aroc: float = 0.15
    # No new position on a stock whose report falls before expiration.
    use_earnings_filter: bool = True

    # Optional indicators, off by default.
    # Trend: underlying close above its simple moving average.
    use_trend_filter: bool = False
    trend_sma_days: int = 200
    # Volatility premium: IV30 / HV30 at least this ratio.
    use_iv_hv_filter: bool = False
    min_iv_hv_ratio: float = 1.0

    # Spread construction: long leg this many dollars below the short leg, first width in this
    # order whose max loss fits the risk budget of one trade.
    spread_widths: tuple[float, ...] = (5.0, 2.5, 2.0, 1.0)
    min_credit: float = 0.15

    # Risk, as shares of the capital. Three measures are kept apart:
    # - collateral: cash the broker holds (spread max loss, CSP strike x 100);
    # - contractual max loss: the most the contract can lose (stock to 0 for a short put);
    # - stress loss: the loss if the underlying gaps down by the stress move.
    # A trade's risk is its max loss for a spread, its stress loss for a short put.
    max_trade_risk_pct: float = 0.01
    # Exceptional deals (score at least `exceptional_min_score`) may use this larger budget.
    use_exceptional_risk: bool = False
    exceptional_trade_risk_pct: float = 0.02
    exceptional_min_score: float = 0.85
    # Sum of the contractual max loss of open, pending and new positions.
    max_open_risk_pct: float = 0.10
    # Sum of the risk of positions in one correlated cluster (index ETFs, a sector).
    max_cluster_risk_pct: float = 0.05
    # Risk of the positions sharing one expiration date.
    max_expiration_risk_pct: float = 0.05
    stress_move_etf: float = 0.15
    stress_move_stock: float = 0.30
    # Collateral caps (cash usage), per trade and in total.
    max_trade_pct: float = 0.10
    max_engaged_pct: float = 0.50
    max_deals: int = 5
    # Positions per sector, open and pending ones included.
    use_sector_limit: bool = True
    max_per_sector: int = 2
    # Days without a new entry on an underlying after its position closed (0 = same day).
    reentry_cooldown_days: int = 0

    # Safety: no new entry while one of these fails (losses measured on the account value).
    max_daily_loss_pct: float = 0.02
    max_monthly_loss_pct: float = 0.04
    max_drawdown_pct: float = 0.10
    # Minutes since the last successful position monitor pass and worker run.
    max_monitor_age_minutes: int = 30
    max_worker_age_minutes: int = 60
    # Fresh quotes are fetched on acceptance: a credit this much below the proposal's is stale.
    max_credit_drift_pct: float = 0.25

    # Exits.
    use_take_profit: bool = True
    take_profit_pct: float = 0.50
    use_stop_loss: bool = True
    stop_loss_multiple: float = 2.0
    use_time_exit: bool = True
    exit_dte: int = 21

    # IV Rank: real IV history once enough days are stored, else a realized-volatility proxy.
    iv_rank_min_history: int = 120
    risk_free_rate: float = 0.04

    # Quality scores, each in [0, 1] (see app.domain.scoring). The final score is the lowest of
    # the absolute quality, execution quality and portfolio fit scores; a deal below
    # `min_quality_score` on any of them is NO TRADE. The weights split each score.
    score_weight_execution: float = 0.25
    score_weight_safety: float = 0.20
    score_weight_return: float = 0.15
    score_weight_time: float = 0.10
    score_weight_regime: float = 0.10
    score_weight_portfolio: float = 0.15
    score_weight_data: float = 0.05
    min_quality_score: float = 0.30

    def trade_risk_pct(self, score: float | None = None) -> float:
        """Risk budget of one trade as a share of capital (the exceptional one if it qualifies)."""
        if self.use_exceptional_risk and score is not None and score >= self.exceptional_min_score:
            return max(self.max_trade_risk_pct, self.exceptional_trade_risk_pct)
        return self.max_trade_risk_pct

    def group_of(self, symbol: str) -> str | None:
        """Group of a symbol in an enabled group, None when it is not traded.

        A stock listed both ways goes to the True Wheel: owning it was accepted explicitly.
        """
        if self.enable_etfs and symbol in self.etfs:
            return ETF_GROUP
        if self.enable_large_caps and symbol in self.large_caps:
            return LARGE_CAP_GROUP
        if self.enable_true_wheel and symbol in self.true_wheel:
            return TRUE_WHEEL_GROUP
        if self.enable_wheel and symbol in self.wheel:
            return SHORT_PUT_GROUP
        return None

    def assignment_accepted(self, symbol: str) -> bool:
        return self.group_of(symbol) == TRUE_WHEEL_GROUP

    def holding_window(self, dte: int, assignment_accepted: bool = False) -> int:
        """Days between entry and the planned exit: entry DTE - exit DTE.

        A True Wheel put has no time exit (it may be assigned), so its window is its DTE.
        """
        if assignment_accepted or not self.use_time_exit:
            return dte
        return dte - self.exit_dte

    @property
    def universe(self) -> tuple[str, ...]:
        groups = (
            (self.enable_etfs, self.etfs),
            (self.enable_large_caps, self.large_caps),
            (self.enable_true_wheel, self.true_wheel),
            (self.enable_wheel, self.wheel),
        )
        return tuple(dict.fromkeys(s for on, symbols in groups if on for s in symbols))

    @property
    def all_symbols(self) -> tuple[str, ...]:
        """Every listed symbol, enabled group or not (what the backtest history must cover)."""
        return tuple(dict.fromkeys(self.etfs + self.large_caps + self.true_wheel + self.wheel))

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyParams":
        """Build from stored JSON; unknown keys are ignored, missing keys take the defaults."""
        known = {f.name for f in fields(cls)}
        values = {k: tuple(v) if isinstance(v, list) else v for k, v in data.items() if k in known}
        return cls(**values)

    def errors(self) -> list[str]:
        """Inconsistent values, as messages for the settings page (empty when valid)."""
        out = []
        for spec in PARAM_SPECS:
            value = getattr(self, spec.key)
            if spec.kind in ("int", "float"):
                if spec.minimum is not None and value < spec.minimum:
                    out.append(f"{spec.label} : minimum {spec.minimum:g}.")
                if spec.maximum is not None and value > spec.maximum:
                    out.append(f"{spec.label} : maximum {spec.maximum:g}.")
        if self.dte_min > self.dte_max:
            out.append("DTE min doit être inférieur ou égal au DTE max.")
        if self.delta_min > self.delta_max:
            out.append("Delta min doit être inférieur ou égal au delta max.")
        if self.max_trade_pct > self.max_engaged_pct:
            out.append("Le maximum par trade dépasse la limite d'engagement totale.")
        if self.max_trade_risk_pct > self.max_cluster_risk_pct:
            out.append("Le risque d'un trade dépasse la limite par cluster.")
        if self.max_cluster_risk_pct > self.max_open_risk_pct:
            out.append("La limite par cluster dépasse la perte maximale ouverte totale.")
        if self.use_exceptional_risk and self.exceptional_trade_risk_pct < self.max_trade_risk_pct:
            out.append("Le seuil exceptionnel doit être au moins égal au risque normal d'un trade.")
        if self.use_time_exit and self.exit_dte >= self.dte_min:
            out.append("La sortie en DTE doit être inférieure au DTE min d'entrée.")
        if self.use_time_exit and self.dte_max - self.exit_dte < self.min_holding_days:
            out.append(
                "Aucune échéance ne laisse la fenêtre de détention minimale : "
                "DTE max - sortie à DTE doit atteindre la fenêtre minimale."
            )
        if not self.universe:
            out.append("Aucun titre à trader : active au moins un groupe non vide.")
        if not self.spread_widths and self.enable_etfs + self.enable_large_caps:
            out.append("Il faut au moins une largeur de spread.")
        absolute = (
            self.score_weight_safety
            + self.score_weight_return
            + self.score_weight_time
            + self.score_weight_regime
            + self.score_weight_data
        )
        if absolute <= 0:
            out.append("Au moins un poids de la qualité absolue doit être positif.")
        if self.max_trade_risk_pct > self.max_expiration_risk_pct:
            out.append("Le risque d'un trade dépasse la limite par échéance.")
        return out


Kind = Literal["bool", "int", "float", "pct", "symbols", "floats"]


@dataclass(frozen=True)
class ParamSpec:
    """How the settings page shows a parameter. `toggle` names the switch that enables it."""

    key: str
    label: str
    group: str
    kind: Kind
    help: str = ""
    toggle: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


PARAM_GROUPS = (
    ("universe", "Stratégies et univers"),
    ("experimental", "Mode expérimental"),
    ("filters", "Filtres des contrats"),
    ("indicators", "Indicateurs optionnels"),
    ("structure", "Construction des spreads"),
    ("risk", "Risque et taille"),
    ("safety", "Garde-fous (blocage des entrées)"),
    ("exits", "Sorties"),
    ("score", "Score de classement"),
)

PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec("enable_etfs", "ETF : put credit spreads", "universe", "bool"),
    ParamSpec("etfs", "Liste des ETF", "universe", "symbols", toggle="enable_etfs"),
    ParamSpec("enable_wheel", "Short Put Income", "universe", "bool",
              "Puts vendues puis rachetées avant l'échéance (sortie à 21 DTE) : pas une wheel, "
              "l'assignation n'est pas recherchée."),
    ParamSpec("wheel", "Titres Short Put Income", "universe", "symbols", toggle="enable_wheel"),
    ParamSpec("enable_true_wheel", "True Wheel", "universe", "bool",
              "Assignation acceptée : les actions sont gardées et des covered calls vendus. "
              "Pas de stop ni de sortie à 21 DTE sur la put."),
    ParamSpec("true_wheel", "Actions que j'accepte de détenir (True Wheel)", "universe",
              "symbols", "Vide par défaut : seuls ces titres reçoivent des puts True Wheel.",
              toggle="enable_true_wheel"),
    ParamSpec("use_wheel_max_strike", "Filtre de strike max (puts vendues)", "universe", "bool",
              "Facultatif : la taille vient du budget de risque, pas du prix de l'action."),
    ParamSpec("wheel_max_strike", "Strike max ($)", "universe", "float",
              toggle="use_wheel_max_strike", minimum=1, maximum=500, step=1),
    ParamSpec("enable_large_caps", "Grandes valeurs : put credit spreads (expérimental)",
              "experimental", "bool",
              "Déficitaires dans toutes les variantes du backtest 2019-2026. Hors des profils "
              "standards."),
    ParamSpec("large_caps", "Liste des grandes valeurs", "experimental", "symbols",
              toggle="enable_large_caps"),
    ParamSpec("dte_min", "DTE min à l'entrée", "filters", "int", minimum=1, maximum=365),
    ParamSpec("dte_max", "DTE max à l'entrée", "filters", "int", minimum=1, maximum=365),
    ParamSpec("min_holding_days", "Fenêtre de détention min (jours)", "filters", "int",
              "DTE d'entrée moins DTE de sortie : rejette les entrées trop proches de la sortie.",
              minimum=0, maximum=365),
    ParamSpec("delta_min", "Delta absolu min de la jambe vendue", "filters", "float",
              "Plage indicative : le delta n'est pas une probabilité de gain.",
              minimum=0.01, maximum=0.9, step=0.01),
    ParamSpec("delta_max", "Delta absolu max de la jambe vendue", "filters", "float",
              minimum=0.01, maximum=0.9, step=0.01),
    ParamSpec("use_iv_rank_filter", "Filtre IV Rank", "filters", "bool",
              "Volatilité implicite élevée par rapport à son année passée."),
    ParamSpec("min_iv_rank", "IV Rank min", "filters", "float", toggle="use_iv_rank_filter",
              minimum=0, maximum=100, step=1),
    ParamSpec("use_open_interest_filter", "Filtre open interest", "filters", "bool"),
    ParamSpec("min_open_interest", "Open interest min", "filters", "int",
              toggle="use_open_interest_filter", minimum=0),
    ParamSpec("use_volume_filter", "Filtre volume", "filters", "bool"),
    ParamSpec("min_volume", "Volume min du jour", "filters", "int", toggle="use_volume_filter",
              minimum=0),
    ParamSpec("use_spread_filter", "Filtre écart bid/ask", "filters", "bool"),
    ParamSpec("max_spread_pct", "Écart bid/ask max", "filters", "pct", toggle="use_spread_filter",
              minimum=0.01, maximum=1, step=0.01),
    ParamSpec("use_earnings_filter", "Filtre résultats trimestriels", "filters", "bool",
              "Pas d'entrée sur une action qui publie avant l'échéance."),
    ParamSpec("use_ror_filter", "Filtre rendement sur risque (non annualisé)", "filters", "bool",
              "Crédit / perte max pour un spread, crédit / cash immobilisé pour une put."),
    ParamSpec("min_ror_spread", "Rendement sur risque min (spread)", "filters", "pct",
              toggle="use_ror_filter", minimum=0, maximum=1, step=0.01),
    ParamSpec("min_ror_put", "Rendement sur cash min (put)", "filters", "pct",
              toggle="use_ror_filter", minimum=0, maximum=1, step=0.005),
    ParamSpec("use_aroc_filter", "Filtre rendement annualisé (AROC, informatif)", "filters",
              "bool", "Annualiser favorise les échéances courtes : désactivé par défaut."),
    ParamSpec("min_aroc", "AROC min", "filters", "pct", toggle="use_aroc_filter",
              minimum=0, maximum=5, step=0.01),
    ParamSpec("use_trend_filter", "Filtre de tendance", "indicators", "bool",
              "Le sous-jacent clôture au-dessus de sa moyenne mobile."),
    ParamSpec("trend_sma_days", "Moyenne mobile (jours)", "indicators", "int",
              toggle="use_trend_filter", minimum=5, maximum=250),
    ParamSpec("use_iv_hv_filter", "Filtre prime de volatilité (IV/HV)", "indicators", "bool",
              "Volatilité implicite au-dessus de la volatilité réalisée. En backtest, la "
              "volatilité implicite des actions est reconstruite depuis la réalisée : "
              "le filtre n'y est vraiment informatif que pour SPY et QQQ."),
    ParamSpec("min_iv_hv_ratio", "IV / HV min", "indicators", "float", toggle="use_iv_hv_filter",
              minimum=0, maximum=5, step=0.05),
    ParamSpec("spread_widths", "Largeurs essayées ($, par ordre de préférence)", "structure",
              "floats"),
    ParamSpec("min_credit", "Crédit min d'un spread ($ par action)", "structure", "float",
              minimum=0, maximum=10, step=0.05),
    ParamSpec("max_trade_risk_pct", "Risque max d'un trade", "risk", "pct",
              "Perte max d'un spread, perte en stress d'une put vendue. Contrats = arrondi "
              "inférieur (budget / risque d'un contrat).", minimum=0.001, maximum=0.2, step=0.001),
    ParamSpec("use_exceptional_risk", "Seuil exceptionnel", "risk", "bool",
              "Budget plus large pour un deal au score très élevé."),
    ParamSpec("exceptional_trade_risk_pct", "Risque max d'un deal exceptionnel", "risk", "pct",
              toggle="use_exceptional_risk", minimum=0.001, maximum=0.2, step=0.001),
    ParamSpec("exceptional_min_score", "Score min d'un deal exceptionnel", "risk", "float",
              toggle="use_exceptional_risk", minimum=0, maximum=1, step=0.01),
    ParamSpec("max_open_risk_pct", "Perte max ouverte totale", "risk", "pct",
              "Somme des pertes maximales contractuelles des positions ouvertes, en attente et "
              "du nouveau deal.", minimum=0.005, maximum=1, step=0.005),
    ParamSpec("max_cluster_risk_pct", "Risque max par cluster corrélé", "risk", "pct",
              "ETF indiciels ensemble, puis chaque secteur.", minimum=0.005, maximum=1,
              step=0.005),
    ParamSpec("max_expiration_risk_pct", "Risque max par échéance", "risk", "pct",
              "Risque des positions de même échéance (cluster d'échéance).", minimum=0.005,
              maximum=1, step=0.005),
    ParamSpec("stress_move_etf", "Choc de stress ETF", "risk", "pct",
              "Baisse du sous-jacent utilisée pour la perte en stress.", minimum=0.01,
              maximum=1, step=0.01),
    ParamSpec("stress_move_stock", "Choc de stress actions", "risk", "pct", minimum=0.01,
              maximum=1, step=0.01),
    ParamSpec("max_trade_pct", "Collatéral max par trade", "risk", "pct", minimum=0.01,
              maximum=1, step=0.01),
    ParamSpec("max_engaged_pct", "Collatéral engagé max", "risk", "pct", minimum=0.01,
              maximum=1, step=0.01),
    ParamSpec("max_deals", "Deals max par jour", "risk", "int", minimum=0, maximum=50),
    ParamSpec("use_sector_limit", "Limite par secteur", "risk", "bool",
              "Positions ouvertes et en attente comprises."),
    ParamSpec("max_per_sector", "Positions max par secteur", "risk", "int",
              toggle="use_sector_limit", minimum=1, maximum=50),
    ParamSpec("reentry_cooldown_days", "Délai avant de revenir sur un titre (jours)", "risk",
              "int", "Après la clôture d'une position ; 0 autorise le jour même.",
              minimum=0, maximum=60),
    ParamSpec("max_daily_loss_pct", "Perte journalière max", "safety", "pct",
              "Valeur du compte depuis la veille.", minimum=0.001, maximum=1, step=0.001),
    ParamSpec("max_monthly_loss_pct", "Stop mensuel", "safety", "pct",
              "Valeur du compte depuis la fin du mois précédent.", minimum=0.001, maximum=1,
              step=0.001),
    ParamSpec("max_drawdown_pct", "Drawdown max", "safety", "pct",
              "Depuis le plus haut du compte. Sert aussi à l'avertissement d'activation d'un "
              "profil.", minimum=0.01, maximum=1, step=0.01),
    ParamSpec("max_monitor_age_minutes", "Dernier passage du moniteur (minutes max)", "safety",
              "int", minimum=5, maximum=1440),
    ParamSpec("max_worker_age_minutes", "Dernier passage du worker (minutes max)", "safety",
              "int", minimum=5, maximum=1440),
    ParamSpec("max_credit_drift_pct", "Baisse max du crédit avant acceptation", "safety", "pct",
              "Écart entre le crédit proposé et le crédit coté à l'acceptation.", minimum=0.01,
              maximum=1, step=0.01),
    ParamSpec("use_take_profit", "Objectif de gain", "exits", "bool"),
    ParamSpec("take_profit_pct", "Part du crédit encaissée", "exits", "pct",
              toggle="use_take_profit", minimum=0.05, maximum=1, step=0.05),
    ParamSpec("use_stop_loss", "Stop (hors covered calls)", "exits", "bool"),
    ParamSpec("stop_loss_multiple", "Rachat à x fois le crédit", "exits", "float",
              toggle="use_stop_loss", minimum=1.1, maximum=20, step=0.1),
    ParamSpec("use_time_exit", "Sortie avant l'échéance", "exits", "bool",
              "Sans elle, la position va à l'échéance."),
    ParamSpec("exit_dte", "Sortie à DTE", "exits", "int", toggle="use_time_exit", minimum=0,
              maximum=180),
    ParamSpec("score_weight_execution", "Poids exécution et liquidité", "score", "float",
              "Écart bid/ask, écart mid/naturel, open interest.", minimum=0, maximum=1,
              step=0.05),
    ParamSpec("score_weight_safety", "Poids marge de sécurité", "score", "float",
              "Distance du point mort en écarts-types.", minimum=0, maximum=1, step=0.05),
    ParamSpec("score_weight_return", "Poids rendement ajusté du risque", "score", "float",
              "Rendement sur risque non annualisé.", minimum=0, maximum=1, step=0.05),
    ParamSpec("score_weight_time", "Poids fenêtre temporelle", "score", "float",
              "Pénalise une détention trop courte ou trop longue.", minimum=0, maximum=1,
              step=0.05),
    ParamSpec("score_weight_regime", "Poids régime de marché", "score", "float",
              "IV Rank, IV/HV, tendance.", minimum=0, maximum=1, step=0.05),
    ParamSpec("score_weight_portfolio", "Poids adéquation au portefeuille", "score", "float",
              "Usage des limites après ajout du deal.", minimum=0, maximum=1, step=0.05),
    ParamSpec("score_weight_data", "Poids qualité des données", "score", "float", minimum=0,
              maximum=1, step=0.05),
    ParamSpec("min_quality_score", "Score min sur chaque note", "score", "float",
              "Sous ce seuil (qualité absolue, exécution ou portefeuille) : NO TRADE.",
              minimum=0, maximum=1, step=0.05),
)  # fmt: skip


class InvalidParams(ValueError):
    """Every problem found in a settings form, in French."""


def _items(raw: Any) -> list[str]:
    values = raw.replace(";", ",").split(",") if isinstance(raw, str) else list(raw)
    return [str(v).strip() for v in values if str(v).strip()]


def parse_params(data: dict[str, Any], base: StrategyParams | None = None) -> StrategyParams:
    """Typed parameters from a settings form, over `base` (the defaults when None).

    Symbols are upper-cased and de-duplicated; lists also accept comma-separated text; unknown
    keys are ignored. Raises InvalidParams listing every invalid or inconsistent value.
    """
    values = (base or StrategyParams()).to_dict()
    problems: list[str] = []
    for spec in PARAM_SPECS:
        if spec.key not in data:
            continue
        raw = data[spec.key]
        try:
            if spec.kind == "bool":
                if not isinstance(raw, bool):
                    raise ValueError
                values[spec.key] = raw
            elif isinstance(raw, bool):
                raise ValueError
            elif spec.kind == "int":
                number = float(raw)
                if number != int(number):
                    raise ValueError
                values[spec.key] = int(number)
            elif spec.kind in ("float", "pct"):
                number = float(raw)
                if number != number:  # NaN
                    raise ValueError
                values[spec.key] = number
            elif spec.kind == "symbols":
                symbols = [s.upper() for s in _items(raw)]
                if any(not s.replace(".", "").replace("-", "").isalnum() for s in symbols):
                    raise ValueError
                values[spec.key] = list(dict.fromkeys(symbols))
            elif spec.kind == "floats":
                widths = [float(x) for x in _items(raw)]
                if any(not w > 0 for w in widths):
                    raise ValueError
                values[spec.key] = list(dict.fromkeys(widths))
        except (TypeError, ValueError, AttributeError):
            problems.append(f"{spec.label} : valeur invalide.")
    if problems:
        raise InvalidParams(" ".join(problems))
    params = StrategyParams.from_dict(values)
    errors = params.errors()
    if errors:
        raise InvalidParams(" ".join(errors))
    return params
