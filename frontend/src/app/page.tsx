import Link from "next/link";

import { PnlCurve, PremiumBars } from "@/components/Charts";
import { ApiDown, Stat } from "@/components/Stat";
import { getDashboard, getHealth } from "@/lib/api";
import { REASON_LABEL, STRATEGY_LABEL, dateTime, month, pct, pnlClass, signed, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const [board, health] = await Promise.all([getDashboard(), getHealth()]);

  if (!board) {
    return (
      <section className="space-y-6">
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Tableau de bord</h1>
        <ApiDown />
      </section>
    );
  }

  const stats = board.analytics;
  const card = "rounded-2xl border border-line bg-surface p-4";
  const th = "px-2 py-1.5 font-medium";
  const td = "px-2 py-1.5 tabular-nums";

  return (
    <section className="space-y-6">
      <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Tableau de bord</h1>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Capital" value={usd(board.capital)} hint={`Départ ${usd(board.starting_capital)}, réalisé inclus`} />
        <Stat label="Cash disponible" value={usd(board.cash_available)} hint="Capital moins le collatéral" />
        <Stat
          label="Collatéral"
          value={usd(board.collateral)}
          hint={`Bloqué par le courtier, limite ${pct(board.max_engaged_pct)}`}
        />
        <Stat
          label="Perte max ouverte"
          value={usd(board.open_max_loss)}
          valueClass={board.open_max_loss > board.open_max_loss_limit ? "text-danger" : ""}
          hint={`Contractuelle, limite ${usd(board.open_max_loss_limit)}`}
        />
        <Stat
          label="Stress loss"
          value={usd(board.stress_loss)}
          hint={`Choc de baisse des sous-jacents, ${pct(board.capital > 0 ? board.stress_loss / board.capital : 0)} du capital`}
        />
        <Stat label="P&L réalisé" value={signed(board.realized_pnl)} valueClass={pnlClass(board.realized_pnl)} />
        <Stat
          label="P&L latent"
          value={signed(board.unrealized_pnl)}
          valueClass={pnlClass(board.unrealized_pnl)}
          hint="Au dernier mark des positions ouvertes"
        />
        <Stat
          label="Valeur du compte"
          value={usd(board.equity)}
          hint={`Jour ${pct(board.daily_change)} · mois ${pct(board.monthly_change)} · drawdown ${pct(board.drawdown)}`}
        />
      </div>

      <div className={`${card} space-y-3`}>
        <div className="text-sm">Risque par cluster corrélé (limite {usd(board.cluster_limit)} chacun)</div>
        {board.clusters.length === 0 ? <p className="text-xs opacity-60">Aucune position ouverte.</p> : null}
        {board.clusters.map((c) => {
          const share = board.cluster_limit > 0 ? Math.min(c.risk / board.cluster_limit, 1) : 0;
          return (
            <div key={c.cluster}>
              <div className="mb-1 flex justify-between gap-2 text-xs">
                <span>{c.cluster}</span>
                <span className="tabular-nums">{usd(c.risk)}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-line">
                <div
                  className={`h-full ${share >= 0.9 ? "bg-danger" : share >= 0.7 ? "bg-warning" : "bg-success"}`}
                  style={{ width: `${share * 100}%` }}
                />
              </div>
            </div>
          );
        })}
        <p className="text-xs opacity-60">
          Limites : perte du jour {pct(board.limits.daily_loss)}, du mois {pct(board.limits.monthly_loss)}, drawdown{" "}
          {pct(board.limits.drawdown)}, risque d&apos;un trade {pct(board.limits.trade_risk)} du capital.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Positions"
          value={`${board.open_positions} ouverte${board.open_positions > 1 ? "s" : ""}`}
          hint={board.pending_positions ? `${board.pending_positions} ordre(s) en attente d'exécution` : undefined}
        />
        <Stat
          label="Deals du jour"
          value={`${board.proposed_opportunities} à traiter`}
          hint={`Screener : ${dateTime(board.last_screener_run)}`}
        />
      </div>

      <div className="flex flex-wrap gap-3 text-sm">
        <Link href="/opportunites" className="rounded-full bg-grad px-5 py-2.5 font-semibold text-on-accent">
          Voir les opportunités
        </Link>
        <Link
          href="/positions"
          className="rounded-full border border-line-strong px-5 py-2.5 font-semibold hover:border-accent"
        >
          Voir les positions
        </Link>
      </div>

      <h2 className="pt-4 font-display text-lg font-bold">Performance</h2>
      <p className="-mt-4 text-xs opacity-60">
        Sur les positions terminées (P&amp;L réalisé). Le latent des positions ouvertes reste à part, plus haut. Une
        put assignée n&apos;est pas un trade : sa prime réduit le prix de revient des actions, comptées à leur sortie.
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Win rate"
          value={pct(stats.win_rate)}
          hint={`${stats.wins} gagnant${stats.wins > 1 ? "s" : ""} sur ${stats.trades} trade${stats.trades > 1 ? "s" : ""}`}
        />
        <Stat
          label="Primes encaissées"
          value={usd(stats.premium_collected)}
          hint={`Ce mois : ${usd(stats.premium_this_month)}`}
        />
        <Stat
          label="Gain moyen / perte moyenne"
          value={`${signed(stats.avg_win)} / ${signed(stats.avg_loss)}`}
          hint={stats.profit_factor != null ? `Profit factor ${stats.profit_factor.toLocaleString("fr-FR")}` : undefined}
        />
        <Stat
          label="Drawdown max"
          value={usd(stats.max_drawdown)}
          valueClass={stats.max_drawdown > 0 ? pnlClass(-stats.max_drawdown) : ""}
          hint={`${pct(stats.max_drawdown_pct)} depuis le plus haut`}
        />
      </div>

      <div className={card}>
        <div className="mb-2 flex flex-wrap justify-between gap-x-3 gap-y-1 text-sm">
          <span>Courbe de capital réalisé</span>
          <span className={`tabular-nums ${pnlClass(stats.realized_pnl)}`}>{signed(stats.realized_pnl)}</span>
        </div>
        <PnlCurve points={stats.curve} start={board.starting_capital} />
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className={card}>
          <div className="mb-2 text-sm">Primes encaissées par mois</div>
          <PremiumBars months={stats.months} />
        </div>
        <div className={`${card} overflow-x-auto`}>
          <div className="mb-2 text-sm">Par mois</div>
          <table className="w-full text-sm">
            <thead className="text-left font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
              <tr>
                <th className={th}>Mois</th>
                <th className={`${th} text-right`}>Primes</th>
                <th className={`${th} text-right`}>P&amp;L réalisé</th>
                <th className={`${th} text-right`}>Trades</th>
              </tr>
            </thead>
            <tbody>
              {[...stats.months].reverse().map((m) => (
                <tr key={m.month} className="border-t border-line/70">
                  <td className="px-2 py-1.5">{month(m.month)}</td>
                  <td className={`${td} text-right`}>{usd(m.premium)}</td>
                  <td className={`${td} text-right ${pnlClass(m.realized_pnl)}`}>{signed(m.realized_pnl)}</td>
                  <td className={`${td} text-right`}>{m.trades}</td>
                </tr>
              ))}
              {stats.months.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-2 py-3 text-center opacity-60">
                    Rien pour l&apos;instant.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {stats.trades > 0 ? (
        <div className="grid gap-3 md:grid-cols-2">
          <div className={`${card} overflow-x-auto`}>
            <div className="mb-2 text-sm">Par stratégie</div>
            <table className="w-full text-sm">
              <thead className="text-left font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
                <tr>
                  <th className={th}>Stratégie</th>
                  <th className={`${th} text-right`}>Trades</th>
                  <th className={`${th} text-right`}>Win rate</th>
                  <th className={`${th} text-right`}>P&amp;L réalisé</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_strategy.map((row) => (
                  <tr key={row.strategy} className="border-t border-line/70">
                    <td className="px-2 py-1.5">{STRATEGY_LABEL[row.strategy]}</td>
                    <td className={`${td} text-right`}>{row.trades}</td>
                    <td className={`${td} text-right`}>{pct(row.win_rate)}</td>
                    <td className={`${td} text-right ${pnlClass(row.realized_pnl)}`}>{signed(row.realized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${card} overflow-x-auto`}>
            <div className="mb-2 text-sm">Motifs de sortie</div>
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(stats.exit_reasons).map(([reason, count]) => (
                  <tr key={reason} className="border-t border-line/70 first:border-0">
                    <td className="px-2 py-1.5">{REASON_LABEL[reason] ?? reason}</td>
                    <td className={`${td} text-right`}>{count}</td>
                    <td className={`${td} w-16 text-right opacity-60`}>{pct(count / stats.trades)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <p className="text-xs opacity-60">
        API {health?.status ?? "?"} · base {health?.database ?? "?"} · broker {health?.broker_env ?? "?"}
      </p>
    </section>
  );
}
