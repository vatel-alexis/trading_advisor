import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { BreakdownTable } from "@/components/BacktestTables";
import { EquityLines, SERIES_COLORS } from "@/components/LabCharts";
import { RunStatus } from "@/components/RunStatus";
import { SaveAsProfile } from "@/components/SaveAsProfile";
import { ApiDown, Stat } from "@/components/Stat";
import { getBacktest, getProfiles } from "@/lib/api";
import {
  GROUP_LABEL,
  REASON_LABEL,
  STRATEGY_LABEL,
  dateTime,
  day,
  paramValue,
  pct,
  pnlClass,
  price,
  signed,
  usd,
} from "@/lib/format";

export const dynamic = "force-dynamic";

const FUNNEL_LABEL: Record<string, string> = {
  contracts: "Contrats examinés",
  iv_rank: "IV Rank",
  trend: "Tendance",
  iv_hv: "IV / HV",
  dte: "DTE",
  delta: "Delta",
  open_interest: "Open interest",
  volume: "Volume",
  spread: "Écart bid/ask",
  earnings: "Résultats",
  structure: "Construction",
  aroc: "AROC (anciens runs)",
  holding: "Fenêtre de détention",
  return: "Rendement sur risque",
  underlyings: "Meilleur contrat par titre",
  selected: "Deals retenus",
};

const TRADES_SHOWN = 300;

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [run, profiles] = await Promise.all([getBacktest(id), getProfiles()]);
  if (!run) {
    return (
      <section className="space-y-4">
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Backtest</h1>
        <ApiDown />
      </section>
    );
  }
  const s = run.summary;
  const r = run.result;
  const fields = profiles?.fields ?? [];
  const defaults = profiles?.defaults ?? {};
  const changed = fields.filter((f) => JSON.stringify(run.params[f.key]) !== JSON.stringify(defaults[f.key]));
  const busy = run.status === "queued" || run.status === "running";
  const funnel = r ? Object.entries(r.funnel).filter(([k]) => k in FUNNEL_LABEL) : [];
  const dte = (key: string) => `${Number.parseInt(key, 10)} j et plus`;

  return (
    <section className="space-y-6">
      {busy ? <AutoRefresh seconds={3} /> : null}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <Link href="/backtests" className="text-xs text-accent underline decoration-accent/40 underline-offset-4 opacity-60">
            Tous les backtests
          </Link>
          <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">
            Backtest n° {run.id} : {run.profile_name}
          </h1>
          <p className="text-sm opacity-70">
            Du {day(run.start)} au {day(s?.end ?? run.end)}, capital {usd(run.capital)} · lancé le{" "}
            {dateTime(run.created_at)}
            {s?.note ? ` · ${s.note}` : ""}
          </p>
        </div>
        <RunStatus run={run} />
      </div>

      {run.status === "failed" ? (
        <p className="rounded-2xl border border-danger/40 bg-danger/10 p-4 text-sm text-danger">{run.error}</p>
      ) : null}
      {busy ? (
        <p className="text-sm opacity-70">
          {run.status === "queued"
            ? "En attente du worker (il vérifie la file toutes les 15 secondes)."
            : `${run.step ?? "Simulation"} : ${Math.round(run.progress * 100)} %.`}
        </p>
      ) : null}

      {s && r ? (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="Rendement annualisé"
              value={pct(s.cagr)}
              valueClass={pnlClass(s.cagr)}
              hint={`SPY acheté et conservé : ${pct(s.benchmark_cagr)}`}
            />
            <Stat label="Drawdown max" value={pct(s.max_drawdown)} hint={`SPY : ${pct(s.benchmark_drawdown)}`} />
            <Stat label="Valeur finale" value={usd(s.final)} hint={`SPY : ${usd(s.benchmark_final)}`} />
            <Stat label="Sharpe" value={s.sharpe?.toFixed(2) ?? "—"} hint="Journalier, sans taux sans risque" />
            <Stat label="Trades clôturés" value={String(s.trades)} hint={`${s.open_at_end} ouvert(s) à la fin`} />
            <Stat
              label="Gagnants"
              value={pct(s.win_rate)}
              hint={`Profit factor ${s.profit_factor?.toFixed(2) ?? "—"}`}
            />
            <Stat label="Gain / perte moyens" value={`${usd(s.avg_win)} / ${usd(s.avg_loss)}`} />
            <Stat
              label="Durée moyenne"
              value={`${s.avg_days_held.toFixed(0)} jours`}
              hint={`Capital engagé moyen ${pct(s.avg_engaged_pct)}, deal ${pct(s.days_with_deal_pct)} des jours`}
            />
          </div>

          <div className="rounded-2xl border border-line bg-surface p-4">
            <h2 className="mb-2 text-sm font-semibold">Valeur du compte</h2>
            <EquityLines
              capital={run.capital}
              lines={[
                {
                  label: run.profile_name,
                  color: SERIES_COLORS[0],
                  points: r.equity.map((p) => ({ date: p.date, value: p.equity })),
                },
                {
                  label: "SPY acheté et conservé (hors dividendes)",
                  color: "var(--benchmark)",
                  dashed: true,
                  points: r.equity
                    .filter((p) => p.benchmark != null)
                    .map((p) => ({ date: p.date, value: p.benchmark as number })),
                },
              ]}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <h2 className="text-sm font-semibold">Par année civile</h2>
              <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
                <table className="w-full text-sm">
                  <tbody>
                    {r.yearly.map((y) => (
                      <tr key={y.year} className="border-t border-line/70 first:border-0">
                        <td className="px-2 py-1">{y.year}</td>
                        <td className={`px-2 py-1 text-right tabular-nums ${pnlClass(y.return)}`}>{pct(y.return)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="space-y-2">
              <h2 className="text-sm font-semibold">Entonnoir du screener (contrats restants, cumulés)</h2>
              <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
                <table className="w-full text-sm">
                  <tbody>
                    {funnel.map(([k, v]) => (
                      <tr key={k} className="border-t border-line/70 first:border-0">
                        <td className="px-2 py-1">{FUNNEL_LABEL[k]}</td>
                        <td className="px-2 py-1 text-right tabular-nums">{v.toLocaleString("fr-FR")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <BreakdownTable title="Sortie" rows={r.breakdowns.exit_reason} label={(k) => REASON_LABEL[k] ?? k} />
            <BreakdownTable title="Groupe" rows={r.breakdowns.group} label={(k) => GROUP_LABEL[k] ?? k} />
            <BreakdownTable title="Année d'entrée" rows={r.breakdowns.year} />
            <BreakdownTable title="DTE à l'entrée" rows={r.breakdowns.entry_dte} label={dte} />
          </div>
          <details>
            <summary className="cursor-pointer text-sm font-semibold">Par titre</summary>
            <div className="mt-2">
              <BreakdownTable title="Titre" rows={r.breakdowns.underlying} />
            </div>
          </details>
        </>
      ) : null}

      <div className="space-y-2 rounded-2xl border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold">Réglages utilisés</h2>
        {changed.length === 0 ? (
          <p className="text-sm opacity-70">Valeurs par défaut.</p>
        ) : (
          <ul className="grid gap-x-6 gap-y-1 text-sm md:grid-cols-2">
            {changed.map((f) => (
              <li key={f.key}>
                {f.label} : <span className="font-medium">{paramValue(f, run.params[f.key])}</span>{" "}
                <span className="text-xs opacity-50">(défaut {paramValue(f, defaults[f.key])})</span>
              </li>
            ))}
          </ul>
        )}
        {Object.keys(run.model).length ? (
          <p className="text-xs opacity-70">
            Hypothèses modifiées :{" "}
            {Object.entries(run.model)
              .map(([k, v]) => `${k} = ${v}`)
              .join(", ")}
          </p>
        ) : null}
        <SaveAsProfile params={run.params} suggestion={`${run.profile_name} (backtest ${run.id})`} />
      </div>

      {r ? (
        <details>
          <summary className="cursor-pointer text-sm font-semibold">
            Trades ({r.trades.length}
            {r.trades.length > TRADES_SHOWN ? `, les ${TRADES_SHOWN} derniers affichés` : ""})
          </summary>
          <div className="mt-2 overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full text-xs">
              <thead className="bg-surface-2 text-left font-mono uppercase tracking-[0.12em] text-muted">
                <tr>
                  <th className="px-2 py-1.5">Entrée</th>
                  <th className="px-2 py-1.5">Titre</th>
                  <th className="px-2 py-1.5">Strikes</th>
                  <th className="px-2 py-1.5">Échéance</th>
                  <th className="px-2 py-1.5 text-right">Qté</th>
                  <th className="px-2 py-1.5 text-right">Crédit</th>
                  <th className="px-2 py-1.5">Sortie</th>
                  <th className="px-2 py-1.5 text-right">Rachat</th>
                  <th className="px-2 py-1.5 text-right">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {r.trades
                  .slice(-TRADES_SHOWN)
                  .reverse()
                  .map((t, i) => (
                    <tr
                      key={`${t.entry_day}-${t.underlying}-${i}`}
                      className="border-t border-line/70"
                    >
                      <td className="px-2 py-1 whitespace-nowrap">{day(t.entry_day)}</td>
                      <td className="px-2 py-1">
                        <span className="font-medium">{t.underlying}</span>{" "}
                        <span className="opacity-60">{STRATEGY_LABEL[t.strategy]}</span>
                      </td>
                      <td className="px-2 py-1 font-mono">{t.strikes.map((k) => price(k)).join(" / ")}</td>
                      <td className="px-2 py-1 whitespace-nowrap">{day(t.expiration)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{t.quantity}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{price(t.credit)}</td>
                      <td className="px-2 py-1 whitespace-nowrap">
                        {t.exit_day
                          ? `${day(t.exit_day)} · ${REASON_LABEL[t.exit_reason ?? ""] ?? t.exit_reason}`
                          : "Ouvert"}
                      </td>
                      <td className="px-2 py-1 text-right tabular-nums">{price(t.exit_price)}</td>
                      <td className={`px-2 py-1 text-right tabular-nums ${pnlClass(t.pnl)}`}>{signed(t.pnl)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </section>
  );
}
