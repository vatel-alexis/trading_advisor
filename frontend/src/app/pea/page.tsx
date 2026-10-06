import Link from "next/link";

import { EquityLines, SERIES_COLORS } from "@/components/LabCharts";
import { ApiDown, Stat } from "@/components/Stat";
import { getPea, type PeaChange, type PeaLevel, type PeaReport, type PeaWeights } from "@/lib/api";
import { dateTime, day, pct, pnlClass } from "@/lib/format";

export const dynamic = "force-dynamic";

// The banner and this page remind of a new allocation for this many days after the signal.
const ALERT_DAYS = 10;
const PERIODS = ["1", "3", "5", "10", "15"];
const START = 10_000;

type Search = Record<string, string | string[] | undefined>;

const signedPct = (value: number | null | undefined) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${pct(value)}`;

function daysBetween(a: string, b: string) {
  return Math.round((new Date(`${b}T12:00:00`).getTime() - new Date(`${a}T12:00:00`).getTime()) / 86_400_000);
}

function Weights({ weights, report }: { weights: PeaWeights; report: PeaReport }) {
  const rows = report.assets.filter((a) => (weights[a.key] ?? 0) > 0.0001);
  return (
    <ul className="space-y-3">
      {rows.map((a) => {
        const w = weights[a.key];
        return (
          <li key={a.key} className="space-y-1">
            <div className="flex items-baseline justify-between gap-3">
              <div className="min-w-0">
                <div className="font-medium">{a.label}</div>
                <div className="truncate text-xs text-muted">
                  {a.name} · <span className="font-mono">{a.ticker}</span>
                  {a.isin ? <span className="font-mono"> · {a.isin}</span> : null}
                </div>
              </div>
              <div className="font-display text-lg font-bold tabular-nums">{pct(w)}</div>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
              <div className="h-full rounded-full bg-accent" style={{ width: `${Math.round(w * 100)}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Changes({ changes, label }: { changes: PeaChange[]; label: Record<string, string> }) {
  return (
    <ul className="space-y-1 text-sm">
      {changes.map((c) => (
        <li key={c.asset} className="flex flex-wrap items-baseline gap-x-2">
          <b className={c.action === "acheter" ? "text-success" : "text-danger"}>
            {c.action === "acheter" ? "Acheter" : "Vendre"}
          </b>
          <span>{label[c.asset] ?? c.asset}</span>
          <span className="text-muted tabular-nums">
            {pct(c.from)} → {pct(c.to)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function AlertBox({ level, report, label }: { level: PeaLevel; report: PeaReport; label: Record<string, string> }) {
  const recent = daysBetween(report.signal_day, report.as_of) <= ALERT_DAYS;
  return (
    <div className="space-y-3">
      {level.changes.length > 0 ? (
        <div
          className={`rounded-2xl border p-4 ${recent ? "border-warning/50 bg-warning/10" : "border-line bg-surface"}`}
        >
          <p className={`font-semibold ${recent ? "text-warning" : ""}`}>
            Ajustement {recent ? "à faire" : "du mois"} : nouvelle répartition au {day(report.signal_day)}
          </p>
          <p className="mb-2 text-xs text-muted">
            À passer dès que possible (le backtest suppose le jour de bourse suivant), en ordres à cours limité.
          </p>
          <Changes changes={level.changes} label={label} />
        </div>
      ) : (
        <div className="rounded-2xl border border-success/40 bg-success/5 p-4 text-sm">
          <b className="text-success">Aucun changement</b> au signal du {day(report.signal_day)} : garder la
          répartition ci-dessous.
        </div>
      )}
      {report.provisional_day && level.preview_changes.length > 0 ? (
        <div className="rounded-2xl border border-line bg-surface p-4">
          <p className="font-semibold">Signal provisoire au {day(report.provisional_day)}</p>
          <p className="mb-2 text-xs text-muted">
            Si le mois finissait aujourd&apos;hui, la répartition changerait ainsi. Rien à faire avant la fin du mois.
          </p>
          <Changes changes={level.preview_changes} label={label} />
        </div>
      ) : null}
    </div>
  );
}

export default async function Page({ searchParams }: { searchParams: Promise<Search> }) {
  const params = await searchParams;
  const data = await getPea();
  const title = (
    <div>
      <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Portefeuille PEA</h1>
      <p className="text-sm opacity-70">
        Répartition mensuelle d&apos;ETF éligibles PEA, en euros, qui suit les tendances et se replie sur le
        monétaire en baisse. Propositions seulement : aucun ordre n&apos;est passé.
      </p>
    </div>
  );
  if (!data) {
    return (
      <section className="space-y-6">
        {title}
        <ApiDown />
      </section>
    );
  }
  const report = data.report;
  if (!report) {
    return (
      <section className="space-y-6">
        {title}
        <p className="rounded-2xl border border-line bg-surface p-4 text-sm">
          Pas encore de calcul : le worker le fait chaque jour après 18 h (Paris), ou tout de suite avec
          <span className="font-mono"> python -m app.worker pea</span>.
        </p>
      </section>
    );
  }

  const wanted = Array.isArray(params.niveau) ? params.niveau[0] : params.niveau;
  const level = report.levels.find((l) => l.key === wanted) ?? report.levels.find((l) => l.key === "moyen") ?? report.levels[0];
  const label = Object.fromEntries(report.assets.map((a) => [a.key, a.label]));
  const s = level.summary;
  const bench = report.benchmark;
  const card = "rounded-2xl border border-line bg-surface p-4";
  const th = "px-2 py-1.5 font-medium";
  const td = "px-2 py-1.5 tabular-nums";
  const years = Object.keys(s.yearly).sort().reverse();
  const scale = (points: { date: string; value: number }[]) =>
    points.map((p) => ({ date: p.date, value: (p.value / 100) * START }));

  return (
    <section className="space-y-6">
      {title}

      <nav className="flex gap-2" aria-label="Niveau de risque">
        {report.levels.map((l) => (
          <Link
            key={l.key}
            href={`/pea?niveau=${l.key}`}
            aria-current={l.key === level.key ? "page" : undefined}
            className={`flex-1 rounded-full px-3 py-2 text-center text-sm transition-colors ${
              l.key === level.key
                ? "bg-accent/10 font-medium text-accent ring-1 ring-accent/40"
                : "border border-line text-muted hover:text-foreground"
            }`}
          >
            {l.label}
            {l.changes.length > 0 && daysBetween(report.signal_day, report.as_of) <= ALERT_DAYS ? (
              <span className="ml-1 text-warning" aria-label="ajustement à faire">
                ●
              </span>
            ) : null}
          </Link>
        ))}
      </nav>
      <p className="-mt-3 text-sm text-muted">{level.description}</p>

      <AlertBox level={level} report={report} label={label} />

      <div className={card}>
        <h2 className="mb-3 font-display text-lg font-bold">Répartition cible</h2>
        <Weights weights={level.target} report={report} />
        <p className="mt-3 text-xs text-muted">
          Signal du {day(report.signal_day)} · prochain signal le dernier jour de bourse du mois · calcul du{" "}
          {dateTime(data.computed_at)}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Perf. annuelle moyenne"
          value={signedPct(s.cagr)}
          valueClass={pnlClass(s.cagr)}
          hint={`Depuis ${day(s.start)} (S&P 500 : ${signedPct(bench.cagr)})`}
        />
        <Stat
          label="Baisse maximale"
          value={pct(-s.max_drawdown)}
          valueClass="text-danger"
          hint={`S&P 500 : ${pct(-bench.max_drawdown)}`}
        />
        <Stat
          label="Volatilité"
          value={pct(s.volatility)}
          hint={s.worst_year ? `Pire année ${s.worst_year[0]} : ${signedPct(s.worst_year[1])}` : undefined}
        />
        <Stat
          label="Ajustements"
          value={`${(s.adjustments_per_year ?? 0).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} / an`}
          hint={`${s.adjustments} en ${Math.round(s.years)} ans, dont ${s.signal_changes} changements de signal`}
        />
      </div>

      <div className={card}>
        <h2 className="mb-2 font-display text-lg font-bold">Performance par an, selon la durée</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className={th}></th>
                {PERIODS.map((p) => (
                  <th key={p} className={`${th} text-right`}>
                    {p} an{p === "1" ? "" : "s"}
                  </th>
                ))}
                <th className={`${th} text-right`}>Depuis {s.start.slice(0, 4)}</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-line">
                <td className={`${td} font-medium`}>{level.label}</td>
                {PERIODS.map((p) => (
                  <td key={p} className={`${td} text-right ${pnlClass(s.periods[p])}`}>
                    {signedPct(s.periods[p])}
                  </td>
                ))}
                <td className={`${td} text-right ${pnlClass(s.cagr)}`}>{signedPct(s.cagr)}</td>
              </tr>
              <tr className="border-t border-line text-muted">
                <td className={td}>S&amp;P 500 conservé</td>
                {PERIODS.map((p) => (
                  <td key={p} className={`${td} text-right`}>
                    {signedPct(bench.periods[p])}
                  </td>
                ))}
                <td className={`${td} text-right`}>{signedPct(bench.cagr)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className={card}>
        <h2 className="mb-2 font-display text-lg font-bold">10 000 € placés en {s.start.slice(0, 4)}</h2>
        <EquityLines
          currency="EUR"
          capital={START}
          lines={[
            { label: level.label, color: SERIES_COLORS[0], points: scale(level.curve) },
            { label: "S&P 500 conservé", color: SERIES_COLORS[1], dashed: true, points: scale(report.benchmark_curve) },
          ]}
        />
      </div>

      <div className={card}>
        <h2 className="mb-2 font-display text-lg font-bold">Comparer les niveaux</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className={th}>Niveau</th>
                <th className={`${th} text-right`}>10 ans</th>
                <th className={`${th} text-right`}>Depuis {s.start.slice(0, 4)}</th>
                <th className={`${th} text-right`}>Baisse max</th>
                <th className={`${th} text-right`}>Pire année</th>
                <th className={`${th} text-right`}>Ajust./an</th>
              </tr>
            </thead>
            <tbody>
              {[...report.levels.map((l) => ({ label: l.label, s: l.summary })), { label: "S&P 500", s: bench }].map(
                (row) => (
                  <tr key={row.label} className="border-t border-line">
                    <td className={`${td} font-medium`}>{row.label}</td>
                    <td className={`${td} text-right`}>{signedPct(row.s.periods["10"])}</td>
                    <td className={`${td} text-right`}>{signedPct(row.s.cagr)}</td>
                    <td className={`${td} text-right text-danger`}>{pct(-row.s.max_drawdown)}</td>
                    <td className={`${td} text-right`}>
                      {row.s.worst_year ? `${row.s.worst_year[0]} ${signedPct(row.s.worst_year[1])}` : "—"}
                    </td>
                    <td className={`${td} text-right`}>
                      {(row.s.adjustments_per_year ?? 0).toLocaleString("fr-FR", { maximumFractionDigits: 1 })}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div className={card}>
          <h2 className="mb-2 font-display text-lg font-bold">Répartition moyenne depuis {s.start.slice(0, 4)}</h2>
          <Weights weights={s.exposure} report={report} />
        </div>
        <div className={card}>
          <h2 className="mb-2 font-display text-lg font-bold">Tendance des ETF au {day(report.signal_day)}</h2>
          <p className="mb-2 text-xs text-muted">
            Score = moyenne des performances sur {report.assumptions.momentum_months.join(", ")} mois. Un ETF
            n&apos;est retenu que si son score est positif et son cours au-dessus de sa moyenne{" "}
            {report.assumptions.trend_months} mois.
          </p>
          <table className="w-full text-sm">
            <tbody>
              {[...report.scores]
                .sort((a, b) => (b.momentum ?? -9) - (a.momentum ?? -9))
                .map((sc) => (
                  <tr key={sc.asset} className="border-t border-line">
                    <td className={`${td} font-medium`}>{label[sc.asset]}</td>
                    <td className={`${td} text-right ${pnlClass(sc.momentum)}`}>{signedPct(sc.momentum)}</td>
                    <td className={`${td} text-right ${sc.trend_ok ? "text-success" : "text-danger"}`}>
                      {sc.trend_ok ? "Hausse" : "Baisse"}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className={card}>
        <h2 className="mb-2 font-display text-lg font-bold">Derniers ajustements ({level.label})</h2>
        <ul className="divide-y divide-line">
          {level.history.map((h) => (
            <li key={h.day} className="space-y-1 py-2">
              <div className="text-xs text-muted">
                {day(h.day)} · {h.reason === "signal" ? "changement de signal" : "rééquilibrage (écart > 5 points)"}
              </div>
              <Changes changes={h.changes} label={label} />
            </li>
          ))}
        </ul>
      </div>

      <div className={card}>
        <h2 className="mb-2 font-display text-lg font-bold">Performance par année</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className={th}>Année</th>
                <th className={`${th} text-right`}>{level.label}</th>
                <th className={`${th} text-right`}>S&amp;P 500</th>
              </tr>
            </thead>
            <tbody>
              {years.map((y) => (
                <tr key={y} className="border-t border-line">
                  <td className={td}>{y}</td>
                  <td className={`${td} text-right ${pnlClass(s.yearly[y])}`}>{signedPct(s.yearly[y])}</td>
                  <td className={`${td} text-right text-muted`}>{signedPct(bench.yearly[y])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <details className={`${card} text-sm`}>
        <summary className="cursor-pointer font-medium">Hypothèses et limites</summary>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">
          <li>
            Calcul en euros. Avant la création des ETF PEA, leur indice est reconstitué avec l&apos;ETF américain
            équivalent converti en euros ({report.assets
              .filter((a) => a.proxy)
              .map((a) => `${a.label} : ${a.proxy} jusqu'au ${day(a.since)}`)
              .join(", ")}
            ). Le monétaire suit un taux court euro approché jusqu&apos;en octobre 2024.
          </li>
          <li>
            Coût de {pct(report.assumptions.cost)} par euro acheté ou vendu (courtage et écart de cours). Les frais
            annuels des ETF sont dans leurs cours. Pas d&apos;impôt tant que l&apos;argent reste dans le PEA.
          </li>
          <li>
            Signal le dernier jour de bourse du mois, exécuté au cours de clôture suivant. Rééquilibrage aussi
            quand une ligne s&apos;écarte de plus de {Math.round(report.assumptions.drift * 100)} points de sa cible.
          </li>
          <li>
            Les performances passées ne préjugent pas des performances futures. 2007-2026 a été très favorable aux
            actions américaines, et le signal mensuel réagit avec retard aux baisses rapides (2020, 2022).
          </li>
        </ul>
      </details>
    </section>
  );
}
