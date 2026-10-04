"""Strategy and risk parameters. Stored as JSON in strategy_configs, one version per change.

Every filter and exit rule has an on/off switch (`use_*`), so a settings profile can include or
exclude it without losing its threshold. The defaults reproduce the strategy validated after
the Sprint 0 spike; the new indicators (trend, IV/HV) start switched off.
"""

from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

ETFS = ("SPY", "QQQ", "IWM")
LARGE_CAPS = (
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "JPM", "BAC", "XOM",
    "KO", "PFE", "T", "INTC",
)  # fmt: skip
# Cash-secured puts need strike <= 20: 10 % of a 20k account is 2 000 of collateral.
WHEEL = (
    "F", "SOFI", "AAL", "RIVN", "NCLH", "VALE", "ITUB", "HBAN", "KEY", "PCG",
    "CLF", "AGNC", "LYFT", "SNAP", "NIO", "RIG",
)  # fmt: skip


@dataclass(frozen=True)
class StrategyParams:
    # Universe: put credit spreads on ETFs and large caps, the wheel on cheap stocks.
    enable_etfs: bool = True
    enable_large_caps: bool = True
    enable_wheel: bool = True
    etfs: tuple[str, ...] = ETFS
    large_caps: tuple[str, ...] = LARGE_CAPS
    wheel: tuple[str, ...] = WHEEL
    wheel_max_strike: float = 20.0

    # Contract filters (thresholds validated after the Sprint 0 spike).
    dte_min: int = 25
    dte_max: int = 55
    delta_min: float = 0.15
    delta_max: float = 0.30
    use_open_interest_filter: bool = True
    min_open_interest: int = 100
    use_volume_filter: bool = True
    min_volume: int = 10
    use_spread_filter: bool = True
    max_spread_pct: float = 0.15
    use_iv_rank_filter: bool = True
    min_iv_rank: float = 30.0
    use_aroc_filter: bool = True
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

    # Spread construction: long leg this many dollars below the short leg, first width found.
    spread_widths: tuple[float, ...] = (5.0, 10.0, 2.5)
    min_credit: float = 0.25

    # Risk.
    max_trade_pct: float = 0.10
    max_engaged_pct: float = 0.50
    max_deals: int = 5
    use_sector_limit: bool = True
    max_per_sector: int = 2
    # False: the limit counts the day's new deals only; True: open positions count too.
    sector_limit_includes_open: bool = False
    # Days without a new entry on an underlying after its position closed (0 = same day).
    reentry_cooldown_days: int = 0

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

    # Ranking score = weights . (PoP and AROC percentiles within the strategy, IVR/100).
    score_weight_pop: float = 0.4
    score_weight_aroc: float = 0.4
    score_weight_iv_rank: float = 0.2

    def group_of(self, symbol: str) -> str | None:
        """Group of a symbol in an enabled group, None when it is not traded."""
        if self.enable_etfs and symbol in self.etfs:
            return "etf"
        if self.enable_large_caps and symbol in self.large_caps:
            return "large_cap"
        if self.enable_wheel and symbol in self.wheel:
            return "wheel"
        return None

    @property
    def universe(self) -> tuple[str, ...]:
        groups = (
            (self.enable_etfs, self.etfs),
            (self.enable_large_caps, self.large_caps),
            (self.enable_wheel, self.wheel),
        )
        return tuple(dict.fromkeys(s for on, symbols in groups if on for s in symbols))

    @property
    def all_symbols(self) -> tuple[str, ...]:
        """Every listed symbol, enabled group or not (what the backtest history must cover)."""
        return tuple(dict.fromkeys(self.etfs + self.large_caps + self.wheel))

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
        if self.use_time_exit and self.exit_dte >= self.dte_min:
            out.append("La sortie en DTE doit être inférieure au DTE min d'entrée.")
        if not self.universe:
            out.append("Aucun titre à trader : active au moins un groupe non vide.")
        if not self.spread_widths and self.enable_etfs + self.enable_large_caps:
            out.append("Il faut au moins une largeur de spread.")
        if self.score_weight_pop + self.score_weight_aroc + self.score_weight_iv_rank <= 0:
            out.append("Au moins un poids du score doit être positif.")
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
    ("universe", "Univers"),
    ("filters", "Filtres des contrats"),
    ("indicators", "Indicateurs optionnels"),
    ("structure", "Construction des spreads"),
    ("risk", "Risque et taille"),
    ("exits", "Sorties"),
    ("score", "Score de classement"),
)

PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec("enable_etfs", "ETF (put credit spreads)", "universe", "bool"),
    ParamSpec("etfs", "Liste des ETF", "universe", "symbols", toggle="enable_etfs"),
    ParamSpec("enable_large_caps", "Grandes valeurs (put credit spreads)", "universe", "bool"),
    ParamSpec("large_caps", "Liste des grandes valeurs", "universe", "symbols",
              toggle="enable_large_caps"),
    ParamSpec("enable_wheel", "Wheel (cash secured puts)", "universe", "bool"),
    ParamSpec("wheel", "Liste wheel", "universe", "symbols", toggle="enable_wheel"),
    ParamSpec("wheel_max_strike", "Strike max wheel ($)", "universe", "float",
              toggle="enable_wheel", minimum=1, maximum=500, step=1),
    ParamSpec("dte_min", "DTE min à l'entrée", "filters", "int", minimum=1, maximum=365),
    ParamSpec("dte_max", "DTE max à l'entrée", "filters", "int", minimum=1, maximum=365),
    ParamSpec("delta_min", "Delta min de la jambe vendue", "filters", "float",
              minimum=0.01, maximum=0.9, step=0.01),
    ParamSpec("delta_max", "Delta max de la jambe vendue", "filters", "float",
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
    ParamSpec("use_aroc_filter", "Filtre rendement annualisé (AROC)", "filters", "bool"),
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
    ParamSpec("max_trade_pct", "Maximum par trade", "risk", "pct", minimum=0.01, maximum=1,
              step=0.01),
    ParamSpec("max_engaged_pct", "Capital engagé max", "risk", "pct", minimum=0.01, maximum=1,
              step=0.01),
    ParamSpec("max_deals", "Deals max par jour", "risk", "int", minimum=0, maximum=50),
    ParamSpec("use_sector_limit", "Limite par secteur", "risk", "bool"),
    ParamSpec("max_per_sector", "Trades max par secteur", "risk", "int",
              toggle="use_sector_limit", minimum=1, maximum=50),
    ParamSpec("sector_limit_includes_open", "Compter les positions ouvertes dans la limite",
              "risk", "bool", "Sinon seuls les deals du jour comptent.",
              toggle="use_sector_limit"),
    ParamSpec("reentry_cooldown_days", "Délai avant de revenir sur un titre (jours)", "risk",
              "int", "Après la clôture d'une position ; 0 autorise le jour même.",
              minimum=0, maximum=60),
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
    ParamSpec("score_weight_pop", "Poids probabilité de gain", "score", "float",
              "0 retire l'indicateur du score.", minimum=0, maximum=1, step=0.05),
    ParamSpec("score_weight_aroc", "Poids rendement annualisé", "score", "float", minimum=0,
              maximum=1, step=0.05),
    ParamSpec("score_weight_iv_rank", "Poids IV Rank", "score", "float", minimum=0, maximum=1,
              step=0.05),
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
