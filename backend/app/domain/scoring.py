"""Quality scores of a candidate trade, kept apart instead of one opaque number.

- eligibility: blocking rules (a limit, no contract, a note under the minimum). Any blocking
  rule makes the deal NO TRADE, whatever its scores;
- absolute quality: safety margin, risk-adjusted return, time window, market regime and data
  quality, the trade on its own;
- execution quality: bid/ask spreads, mid versus natural credit, open interest;
- portfolio fit: how much of each portfolio limit is used once the deal is added;
- relative rank: its place among the day's candidates.

The final score is the lowest of the three quality scores, so a strong trade cannot hide an
untradeable market or a portfolio already full. Every component is in [0, 1].
Delta and the probabilities computed from it are model estimates, never used as a
probability of profit here: safety comes from the distance to the breakeven in standard
deviations of the expected move.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.params import StrategyParams
from app.domain.pricing import spread_pct

PUT_CREDIT_SPREAD = "put_credit_spread"
COVERED_CALL = "covered_call"

COMPONENT_LABELS = {
    "execution": "Exécution et liquidité",
    "safety": "Marge de sécurité",
    "return": "Rendement ajusté du risque",
    "time": "Fenêtre temporelle",
    "regime": "Régime de marché",
    "portfolio": "Adéquation au portefeuille",
    "data": "Qualité des données",
}
SCORE_LABELS = {
    "absolute": "Qualité absolue",
    "execution": "Qualité d'exécution",
    "portfolio": "Adéquation au portefeuille",
}
# A component under this value is listed among the deal's main weaknesses.
WEAK = 0.5
# Return on risk mapped to [0, 1] between these bounds, per kind of trade.
RETURN_BOUNDS = {"spread": (0.05, 0.40), "put": (0.005, 0.04), "call": (0.005, 0.03)}
# Holding days above the minimum that earn a full time score, then the plateau length.
TIME_RAMP, TIME_PLATEAU = 10, 20


def clip(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class Quality:
    components: dict[str, float]
    absolute: float
    execution: float
    portfolio: float | None = None
    # Weighted sum of the seven components, for information.
    weighted: float | None = None
    final: float | None = None
    # Missing inputs ("donnée absente"), shown as such.
    missing: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    # Blocking reasons (NO TRADE), exact sentences.
    blocking: list[str] = field(default_factory=list)
    rank: int | None = None
    rank_of: int | None = None

    @property
    def pre_score(self) -> float:
        """Score before the portfolio is known: used to pick and order the candidates."""
        return min(self.absolute, self.execution)

    @property
    def eligible(self) -> bool:
        return not self.blocking

    def to_dict(self) -> dict:
        def r(x: float | None) -> float | None:
            return None if x is None else round(x, 4)

        return {
            "eligible": self.eligible,
            "absolute": r(self.absolute),
            "execution": r(self.execution),
            "portfolio": r(self.portfolio),
            "final": r(self.final),
            "weighted": r(self.weighted),
            "components": {k: r(v) for k, v in self.components.items()},
            "missing": self.missing,
            "weaknesses": self.weaknesses,
            "blocking": self.blocking,
            "rank": self.rank,
            "rank_of": self.rank_of,
        }


@dataclass(frozen=True)
class LegQuote:
    """What scoring needs from a leg's quote."""

    bid: float
    ask: float
    iv: float
    open_interest: int
    volume: int


def return_on_risk(strategy: str, credit: float, max_loss: float, collateral: float) -> float:
    """Return over the whole trade, not annualized: credit / max loss for a spread, credit /
    cash held for a single put. Amounts per unit: credit per share, the others in dollars."""
    base = max_loss if strategy == PUT_CREDIT_SPREAD else collateral
    return credit * 100 / base if base > 0 else 0.0


def sd_distance(spot: float, level: float, iv: float, dte: int) -> float | None:
    """How many standard deviations of the move to expiration separate spot from `level`."""
    if spot <= 0 or level <= 0 or iv <= 0 or dte <= 0:
        return None
    return math.log(spot / level) / (iv * math.sqrt(dte / 365))


def _execution(legs: Sequence[LegQuote], credit: float, natural: float, p: StrategyParams,
               missing: list[str]) -> float:  # fmt: skip
    spreads = [spread_pct(q.bid, q.ask) for q in legs]
    width = sum(spreads) / len(spreads) if spreads else math.inf
    parts = [clip(1 - width / (2 * p.max_spread_pct))]
    # Mid versus natural: what is lost if the order fills at the natural price.
    slip = (credit - natural) / credit if credit > 0 else 1.0
    parts.append(clip(1 - slip / 0.5))
    oi = min((q.open_interest for q in legs), default=0)
    if oi > 0:
        parts.append(clip(math.log10(oi) / 3))
    else:
        missing.append("open interest")
    return sum(parts) / len(parts)


def _time(holding: int, p: StrategyParams) -> float:
    """Full score a little above the minimum holding window, lower when too short or long."""
    low = p.min_holding_days
    if holding < low:
        return 0.0
    if holding < low + TIME_RAMP:
        return 0.3 + 0.7 * (holding - low) / TIME_RAMP
    excess = holding - low - TIME_RAMP - TIME_PLATEAU
    return 1.0 if excess <= 0 else max(0.5, 1 - excess / 40)


def _regime(
    iv_rank: float | None, iv_hv: float | None, trend_up: bool | None, missing: list[str]
) -> float:
    parts = []
    if iv_rank is not None:
        parts.append(clip(iv_rank / 100))
    else:
        missing.append("IV Rank")
    if iv_hv is not None:
        parts.append(clip((iv_hv - 0.8) / 0.6))
    if trend_up is not None:
        parts.append(1.0 if trend_up else 0.0)
    return sum(parts) / len(parts) if parts else 0.5


def assess(
    *,
    strategy: str,
    spot: float,
    breakeven: float,
    strike: float,
    dte: int,
    holding_window: int,
    credit: float,
    natural_credit: float,
    max_loss: float,
    collateral: float,
    legs: Sequence[LegQuote],
    iv_rank: float | None,
    iv_rank_method: str | None,
    iv_hv: float | None,
    hv: float | None,
    trend_up: bool | None,
    params: StrategyParams,
    cost_basis: float | None = None,
) -> Quality:
    """The scores that do not depend on the portfolio."""
    p = params
    missing: list[str] = []
    execution = _execution(legs, credit, natural_credit, p, missing)

    iv = legs[0].iv if legs else 0.0
    if strategy == COVERED_CALL:
        sd = sd_distance(strike, spot, iv, dte)  # room before the shares are called away
        ror = credit / cost_basis if cost_basis else 0.0
        kind = "call"
    else:
        sd = sd_distance(spot, breakeven, iv, dte)
        ror = return_on_risk(strategy, credit, max_loss, collateral)
        kind = "spread" if strategy == PUT_CREDIT_SPREAD else "put"
    if sd is None:
        missing.append("volatilité implicite")
    safety = clip(((sd or 0.0) - 0.5) / 1.0)
    low, high = RETURN_BOUNDS[kind]
    ret = clip((ror - low) / (high - low))
    time = 1.0 if strategy == COVERED_CALL else _time(holding_window, p)
    regime = _regime(iv_rank, iv_hv, trend_up, missing)

    data = 1.0
    if iv_rank is None:
        data -= 0.4
    elif iv_rank_method == "hv_proxy":
        data -= 0.3
        missing.append("historique d'IV (IV Rank estimé par la volatilité réalisée)")
    if "open interest" in missing:
        data -= 0.3
    if legs and all(q.volume <= 0 for q in legs):
        data -= 0.2
        missing.append("volume")
    if hv is None:
        data -= 0.2
        missing.append("volatilité réalisée")
    data = clip(data)

    components = {
        "execution": execution,
        "safety": safety,
        "return": ret,
        "time": time,
        "regime": regime,
        "data": data,
    }
    weights = {
        "safety": p.score_weight_safety,
        "return": p.score_weight_return,
        "time": p.score_weight_time,
        "regime": p.score_weight_regime,
        "data": p.score_weight_data,
    }
    total = sum(weights.values())
    absolute = sum(components[k] * w for k, w in weights.items()) / total if total > 0 else 0.0
    return Quality(
        components=components,
        absolute=absolute,
        execution=execution,
        missing=missing,
        weaknesses=weaknesses(components),
    )


def weaknesses(components: dict[str, float]) -> list[str]:
    """Labels of the weak components, weakest first."""
    ordered = sorted(components.items(), key=lambda kv: kv[1])
    return [COMPONENT_LABELS[k] for k, v in ordered if v < WEAK]


def portfolio_fit(utilization: float) -> float:
    """1 while the most used limit stays at half or less after the deal, then down to 0.3 at
    the limit itself: the hard limits block, the fit only ranks."""
    return clip(1 - 1.4 * max(0.0, utilization - 0.5))


def finalize(quality: Quality, fit: float | None, params: StrategyParams) -> Quality:
    """Add the portfolio fit (None when the deal was never sized), compute the final score and
    the blocking rules."""
    p = params
    quality.portfolio = fit
    scores = {"absolute": quality.absolute, "execution": quality.execution}
    if fit is not None:
        quality.components["portfolio"] = fit
        scores["portfolio"] = fit
    quality.final = min(scores.values())
    weights = {
        "execution": p.score_weight_execution,
        "safety": p.score_weight_safety,
        "return": p.score_weight_return,
        "time": p.score_weight_time,
        "regime": p.score_weight_regime,
        "portfolio": p.score_weight_portfolio,
        "data": p.score_weight_data,
    }
    weights = {k: w for k, w in weights.items() if k in quality.components}
    total = sum(weights.values())
    quality.weighted = (
        sum(quality.components[k] * w for k, w in weights.items()) / total if total > 0 else None
    )
    quality.weaknesses = weaknesses(quality.components)
    for key, value in scores.items():
        if value < p.min_quality_score:
            quality.blocking.append(
                f"{SCORE_LABELS[key]} {value:.2f} sous le minimum {p.min_quality_score:.2f}"
            )
    return quality
