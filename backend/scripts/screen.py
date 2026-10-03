"""Run the screener on live Yahoo data and print the funnel and the day's deals, without the DB.

Usage (needs network access to Yahoo Finance):
    python -m scripts.screen               # default universe, empty 20k account
    python -m scripts.screen SPY NVDA F    # custom tickers

IV Rank falls back to the realized-volatility proxy here, since no IV history is read.
"""

import sys
from datetime import date

from app.domain.params import StrategyParams
from app.domain.risk import AccountState
from app.domain.screener import FUNNEL_STAGES, Candidate, screen
from app.marketdata.yahoo import YahooProvider


def describe(c: Candidate) -> str:  # pragma: no cover - output formatting
    strikes = "/".join(f"{leg.quote.strike:g}" for leg in c.legs)
    ivr = f"{c.iv_rank.value:.0f} ({c.iv_rank.method})" if c.iv_rank else "n/a"
    return (
        f"{c.underlying:<6}{c.strategy:<19}{c.expiration} ({c.dte}j) {strikes:<10}"
        f" x{c.quantity:<3} crédit {c.credit:.2f} (naturel {c.natural_credit:.2f})"
        f"  perte max {c.max_loss * c.quantity:>7.0f}  delta {c.short_delta:+.2f}"
        f"  PoP {c.pop:.0%}  AROC {c.aroc:.0%}  IVR {ivr}  score {c.score:.3f}"
    )


def main(tickers: list[str]) -> None:  # pragma: no cover - network
    params = StrategyParams()
    today = date.today()
    provider = YahooProvider(params.dte_min, params.dte_max)
    snapshots = []
    for ticker in tickers:
        try:
            snapshots.append(provider.snapshot(ticker, today))
        except Exception as exc:
            print(f"{ticker}: erreur {exc}")
    result = screen(snapshots, today, params, AccountState(capital=20_000))
    print("Entonnoir : " + ", ".join(f"{s} {result.funnel[s]}" for s in FUNNEL_STAGES))
    print("\nMeilleur trade par sous-jacent :")
    for c in result.ranked:
        reason = result.skipped.get(c.underlying)
        print(describe(c) + (f"  [écarté : {reason}]" if reason else ""))
    print(f"\n{len(result.selected)} deals retenus.")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:] or list(StrategyParams().universe))
