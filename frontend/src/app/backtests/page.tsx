import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { BacktestLauncher, DeleteRunButton } from "@/components/BacktestLauncher";
import { RunStatus } from "@/components/RunStatus";
import { ApiDown } from "@/components/Stat";
import { getBacktests, getProfiles } from "@/lib/api";
import { dateTime, day, pct, pnlClass, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [data, profiles] = await Promise.all([getBacktests(), getProfiles()]);
  if (!data || !profiles) {
    return (
      <section className="space-y-4">
        <h1 className="text-2xl font-semibold">Backtests</h1>
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
        <h1 className="text-2xl font-semibold">Backtests</h1>
        <p className="text-sm opacity-70">
          Rejoue un profil de réglages jour par jour depuis 2019 sur des prix d&apos;options reconstruits. Les profils
          se modifient dans{" "}
          <Link href="/reglages" className="underline underline-offset-4">
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
        <p className="rounded-lg border border-black/10 p-4 text-sm opacity-70 dark:border-white/10">
          Aucun backtest pour l&apos;instant.
        </p>
      ) : (
        <form method="get" action="/backtests/comparer" className="space-y-2">
          <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/10">
            <table className="w-full text-sm">
              <thead className="bg-black/5 text-left text-xs uppercase tracking-wide opacity-70 dark:bg-white/5">
                <tr>
                  <th className={th}>Comparer</th>
                  <th className={th}>N°</th>
                  <th className={th}>Profil</th>
                  <th className={th}>Période</th>
                  <th className={th}>Statut</th>
                  <th className={`${th} text-right`}>Rendement/an</th>
                  <th className={`${th} text-right`}>Drawdown max</th>
                  <th className={`${th} text-right`}>Sharpe</th>
                  <th className={`${th} text-right`}>Trades</th>
                  <th className={`${th} text-right`}>Gagnants</th>
                  <th className={`${th} text-right`}>Final</th>
                  <th className={th}></th>
                </tr>
              </thead>
              <tbody>
                {data.runs.map((r) => {
                  const s = r.summary;
                  return (
                    <tr key={r.id} className="border-t border-black/5 dark:border-white/5">
                      <td className={th}>
                        <input type="checkbox" name="ids" value={r.id} disabled={r.status !== "done"} />
                      </td>
                      <td className={td}>
                        <Link href={`/backtests/${r.id}`} className="underline underline-offset-4">
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
                      </td>
                      <td className={`${th} whitespace-nowrap text-xs`}>
                        {day(r.start)} → {day(s?.end ?? r.end)}
                      </td>
                      <td className={th}>
                        <RunStatus run={r} />
                      </td>
                      <td className={`${td} text-right ${pnlClass(s?.cagr)}`}>{s ? pct(s.cagr) : "—"}</td>
                      <td className={`${td} text-right`}>{s ? pct(s.max_drawdown) : "—"}</td>
                      <td className={`${td} text-right`}>{s?.sharpe?.toFixed(2) ?? "—"}</td>
                      <td className={`${td} text-right`}>{s?.trades ?? "—"}</td>
                      <td className={`${td} text-right`}>{s ? pct(s.win_rate) : "—"}</td>
                      <td className={`${td} text-right`}>{s ? usd(s.final) : "—"}</td>
                      <td className={th}>{r.status !== "running" ? <DeleteRunButton id={r.id} /> : null}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <button className="rounded border border-black/20 px-3 py-1.5 text-sm font-medium hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10">
            Comparer la sélection
          </button>
        </form>
      )}
    </section>
  );
}
