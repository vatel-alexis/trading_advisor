import Link from "next/link";

import { EquityLines, SERIES_COLORS } from "@/components/LabCharts";
import { ApiDown } from "@/components/Stat";
import { getBacktest, getProfiles, type BacktestDetail, type BacktestSummary } from "@/lib/api";
import { day, paramValue, pct, pnlClass, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

type Search = Record<string, string | string[] | undefined>;

const MAX_RUNS = SERIES_COLORS.length;

const METRICS: {
  label: string;
  value: (s: BacktestSummary) => string;
  tone?: (s: BacktestSummary) => number | null;
}[] = [
  { label: "Rendement annualisé", value: (s) => pct(s.cagr), tone: (s) => s.cagr },
  { label: "Drawdown max", value: (s) => pct(s.max_drawdown) },
  { label: "Sharpe", value: (s) => s.sharpe?.toFixed(2) ?? "—" },
  { label: "Valeur finale", value: (s) => usd(s.final) },
  { label: "Trades clôturés", value: (s) => String(s.trades) },
  { label: "Gagnants", value: (s) => pct(s.win_rate) },
  { label: "Profit factor", value: (s) => s.profit_factor?.toFixed(2) ?? "—" },
  { label: "Gain moyen", value: (s) => usd(s.avg_win) },
  { label: "Perte moyenne", value: (s) => usd(s.avg_loss) },
  { label: "Durée moyenne", value: (s) => `${s.avg_days_held.toFixed(0)} j` },
  { label: "Capital engagé moyen", value: (s) => pct(s.avg_engaged_pct) },
  { label: "Période", value: (s) => `${day(s.start)} → ${day(s.end)}` },
];

export default async function Page({ searchParams }: { searchParams: Promise<Search> }) {
  const query = await searchParams;
  const ids = (Array.isArray(query.ids) ? query.ids : query.ids ? [query.ids] : []).slice(0, MAX_RUNS);
  const [profiles, ...fetched] = await Promise.all([getProfiles(), ...ids.map((id) => getBacktest(id))]);
  const runs = fetched.filter((r): r is BacktestDetail => r != null && r.summary != null && r.result != null);

  if (ids.length === 0 || runs.length === 0) {
    return (
      <section className="space-y-4">
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Comparer des backtests</h1>
        {profiles === null ? (
          <ApiDown />
        ) : (
          <p className="text-sm opacity-70">
            Coche des backtests terminés dans la{" "}
            <Link href="/backtests" className="text-accent underline decoration-accent/40 underline-offset-4">
              liste
            </Link>{" "}
            puis « Comparer la sélection ».
          </p>
        )}
      </section>
    );
  }

  const fields = profiles?.fields ?? [];
  const differing = fields.filter((f) => new Set(runs.map((r) => JSON.stringify(r.params[f.key]))).size > 1);
  const years = Array.from(new Set(runs.flatMap((r) => r.result!.yearly.map((y) => y.year)))).sort();
  const name = (r: BacktestDetail) => `n° ${r.id} ${r.profile_name}`;
  const capital = runs[0].capital;
  const first = runs[0];
  const th = "px-3 py-2 text-right font-medium";

  return (
    <section className="space-y-6">
      <div>
        <Link href="/backtests" className="text-xs text-accent underline decoration-accent/40 underline-offset-4 opacity-60">
          Tous les backtests
        </Link>
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Comparer des backtests</h1>
        {new Set(runs.map((r) => `${r.start}${r.summary!.end}${r.capital}`)).size > 1 ? (
          <p className="text-sm text-warning">Attention : les périodes ou les capitaux diffèrent.</p>
        ) : null}
      </div>

      <div className="rounded-2xl border border-line bg-surface p-4">
        <EquityLines
          capital={capital}
          lines={[
            ...runs.map((r, i) => ({
              label: name(r),
              color: SERIES_COLORS[i],
              points: r.result!.equity.map((p) => ({ date: p.date, value: p.equity })),
            })),
            {
              label: "SPY acheté et conservé",
              color: "var(--benchmark)",
              dashed: true,
              points: first
                .result!.equity.filter((p) => p.benchmark != null)
                .map((p) => ({ date: p.date, value: p.benchmark as number })),
            },
          ]}
        />
      </div>

      <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
            <tr>
              <th className="px-3 py-2 text-left font-medium opacity-70">Mesure</th>
              {runs.map((r, i) => (
                <th key={r.id} className={th}>
                  <Link href={`/backtests/${r.id}`} className="hover:underline" style={{ color: SERIES_COLORS[i] }}>
                    {name(r)}
                  </Link>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {METRICS.map((m) => (
              <tr key={m.label} className="border-t border-line/70">
                <td className="px-3 py-1.5">{m.label}</td>
                {runs.map((r) => (
                  <td
                    key={r.id}
                    className={`px-3 py-1.5 text-right tabular-nums ${m.tone ? pnlClass(m.tone(r.summary!)) : ""}`}
                  >
                    {m.value(r.summary!)}
                  </td>
                ))}
              </tr>
            ))}
            {years.map((year) => (
              <tr key={year} className="border-t border-line/70">
                <td className="px-3 py-1.5">Année {year}</td>
                {runs.map((r) => {
                  const ret = r.result!.yearly.find((y) => y.year === year)?.return ?? null;
                  return (
                    <td key={r.id} className={`px-3 py-1.5 text-right tabular-nums ${pnlClass(ret)}`}>
                      {pct(ret)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="space-y-2">
        <h2 className="text-sm font-semibold">Réglages qui diffèrent</h2>
        {differing.length === 0 ? (
          <p className="text-sm opacity-70">Mêmes réglages (seules les hypothèses ou la période changent).</p>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full text-sm">
              <tbody>
                {differing.map((f) => (
                  <tr key={f.key} className="border-t border-line/70 first:border-0">
                    <td className="px-3 py-1.5">{f.label}</td>
                    {runs.map((r) => (
                      <td key={r.id} className="px-3 py-1.5 text-right text-xs">
                        {paramValue(f, r.params[f.key])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
