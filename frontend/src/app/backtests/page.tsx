import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { BacktestLauncher, DeleteRunButton } from "@/components/BacktestLauncher";
import { ROBUSTNESS_LABEL, factor, isEngine2 } from "@/components/BacktestRobustness";
import { RunStatus } from "@/components/RunStatus";
import { ApiDown } from "@/components/Stat";
import { getBacktests, getProfiles } from "@/lib/api";
import { dateTime, day, pct, pnlClass } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [data, profiles] = await Promise.all([getBacktests(), getProfiles()]);
  if (!data || !profiles) {
    return (
      <section className="space-y-4">
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Backtests</h1>
        <ApiDown />
      </section>
    );
  }
  const busy = data.runs.some((r) => r.status === "queued" || r.status === "running");
  const th = "px-3 py-2";
  const td = "px-3 py-2 tabular-nums";

  return (
    <section className="space-y-4">
      {busy ? <AutoRefresh seconds={3} /> : null}
      <div>
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Backtests</h1>
        <p className="text-sm opacity-70">
          Rejoue un profil de réglages jour par jour depuis 2019 sur des prix d&apos;options reconstruits (pas des
          cotations historiques). Les profils se modifient dans{" "}
          <Link href="/reglages" className="text-accent underline decoration-accent/40 underline-offset-4">
            Réglages
          </Link>
          .
          {data.cache
            ? ` Historique en cache : ${day(data.cache.first_day)} → ${day(data.cache.last_day)}, ${data.cache.symbols.length} titres (téléchargé le ${dateTime(data.cache.fetched_at)}).`
            : ""}
        </p>
      </div>

      <BacktestLauncher
        profiles={profiles.profiles}
        defaults={data.defaults}
        modelFields={data.model_fields}
        modelDefaults={data.model_defaults}
        hasCache={data.cache != null}
      />

      {data.runs.length === 0 ? (
        <p className="rounded-2xl border border-line bg-surface p-4 text-sm opacity-70">
          Aucun backtest pour l&apos;instant.
        </p>
      ) : (
        <form method="get" action="/backtests/comparer" className="space-y-2">
          <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
                <tr>
                  <th className={th}>Comparer</th>
                  <th className={th}>N°</th>
                  <th className={th}>Profil</th>
                  <th className={th}>Période</th>
                  <th className={th}>Statut</th>
                  <th className={`${th} text-right`}>Drawdown max</th>
                  <th className={`${th} text-right`}>Profit factor</th>
                  <th className={`${th} text-right`}>Rendement net</th>
                  <th className={`${th} text-right`}>Pire année</th>
                  <th className={`${th} text-right`}>Robustesse</th>
                  <th className={`${th} text-right`}>Rendement/an</th>
                  <th className={`${th} text-right`}>Trades</th>
                  <th className={th}></th>
                </tr>
              </thead>
              <tbody>
                {data.runs.map((r) => {
                  const s = r.summary;
                  return (
                    <tr key={r.id} className="border-t border-line/70">
                      <td className={th}>
                        <input type="checkbox" name="ids" value={r.id} disabled={r.status !== "done"} />
                      </td>
                      <td className={td}>
                        <Link
                          href={`/backtests/${r.id}`}
                          className="text-accent underline decoration-accent/40 underline-offset-4"
                        >
                          {r.id}
                        </Link>
                      </td>
                      <td className={th}>
                        <Link href={`/backtests/${r.id}`} className="font-medium hover:underline">
                          {r.profile_name}
                        </Link>
                        {Object.keys(r.model).length ? (
                          <div className="text-xs opacity-60">hypothèses modifiées</div>
                        ) : null}
                        {s && !isEngine2(s) ? (
                          <div className="text-xs text-warning">ancien moteur, Wheel incomplète</div>
                        ) : null}
                      </td>
                      <td className={`${th} whitespace-nowrap text-xs`}>
                        {day(r.start)} → {day(s?.end ?? r.end)}
                      </td>
                      <td className={th}>
                        <RunStatus run={r} />
                      </td>
                      <td className={`${td} text-right`}>{s ? pct(s.max_drawdown) : "—"}</td>
                      <td className={`${td} text-right`}>{s ? factor(s.profit_factor) : "—"}</td>
                      <td className={`${td} text-right ${pnlClass(s?.net_return)}`}>{pct(s?.net_return)}</td>
                      <td className={`${td} text-right ${pnlClass(s?.worst_year?.return)}`}>
                        {s?.worst_year ? pct(s.worst_year.return) : "—"}
                      </td>
                      <td className={`${td} text-right`}>
                        {s?.robustness ? ROBUSTNESS_LABEL[s.robustness.label] : "—"}
                      </td>
                      <td className={`${td} text-right ${pnlClass(s?.cagr)}`}>{s ? pct(s.cagr) : "—"}</td>
                      <td className={`${td} text-right`}>{s?.trades ?? "—"}</td>
                      <td className={th}>{r.status !== "running" ? <DeleteRunButton id={r.id} /> : null}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <button className="rounded-full border border-line-strong px-3 hover:border-accent py-1.5 text-sm font-medium hover:bg-surface-2">
            Comparer la sélection
          </button>
        </form>
      )}
    </section>
  );
}
