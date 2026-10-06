import type {
  BacktestResult,
  BacktestSummary,
  PeriodFigures,
  Robustness,
  ScenarioFigures,
  StressRow,
  WheelFigures,
} from "@/lib/api";
import { day, pct, pnlClass, signed } from "@/lib/format";
import { Stat } from "@/components/Stat";

export const ROBUSTNESS_LABEL: Record<Robustness["label"], string> = {
  robuste: "Robuste",
  moyen: "Moyenne",
  fragile: "Fragile",
  insuffisant: "Historique insuffisant",
};

const ROBUSTNESS_CLASS: Record<Robustness["label"], string> = {
  robuste: "text-success",
  moyen: "",
  fragile: "text-danger",
  insuffisant: "opacity-70",
};

export const factor = (value: number | null | undefined) => (value == null ? "—" : value.toFixed(2));

export const recoveryText = (days: number | undefined, recovered: boolean | undefined) =>
  days == null ? "—" : days === 0 ? "Aucun recul" : `${days} j${recovered ? "" : " (pas encore récupéré)"}`;

// Runs from before the engine 2 have no complete Wheel and no robustness figures.
export const isEngine2 = (s: BacktestSummary | null | undefined) => !!s?.engine_version && s.engine_version >= 2;

const cell = "px-2 py-1 text-right tabular-nums";
const head = "bg-surface-2 text-left font-mono text-[10px] uppercase tracking-[0.12em] text-muted";

// What the run can and cannot say, shown before any figure.
export function DataBanner({ s, r }: { s: BacktestSummary; r: BacktestResult }) {
  if (!isEngine2(s)) {
    return (
      <div className="rounded-2xl border border-warning/40 bg-warning/10 p-4 text-sm">
        <p className="font-semibold">Ancien moteur : résultats non comparables</p>
        <p className="mt-1 opacity-80">
          Ce backtest date d&apos;avant la Wheel complète, les scénarios d&apos;exécution et les tests de robustesse.
          Les puts assignés n&apos;y suivent pas les actions ni les covered calls. Relance-le pour obtenir des chiffres
          comparables.
        </p>
      </div>
    );
  }
  const q = r.data_quality;
  return (
    <div className="space-y-2 rounded-2xl border border-warning/40 bg-warning/10 p-4 text-sm">
      <p className="font-semibold">
        Prix reconstitués, pas des cotations historiques · confiance des données : {q?.level ?? s.data_quality ?? "—"}
      </p>
      <p className="opacity-80">
        Les primes sont recalculées (Black-Scholes) à partir des cours de clôture et d&apos;un indice de volatilité
        {q?.iv_from_index != null ? ` (indice VIX/VXN pour ${pct(q.iv_from_index)} des trades)` : ""}. Un backtest
        positif ne garantit rien ; regarde d&apos;abord le drawdown, le pire scénario et les stress tests.
      </p>
      {q?.untested_filters.length ? (
        <p className="opacity-80">
          <span className="font-medium">Filtres non testés</span> (pas d&apos;historique) :{" "}
          {q.untested_filters.join(", ")}. Ils sont actifs en réel mais n&apos;ont filtré aucun trade ici.
        </p>
      ) : null}
      {q?.approximations.length ? (
        <details>
          <summary className="cursor-pointer opacity-80">Approximations du modèle</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 opacity-80">
            {q.approximations.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

// Risk first: drawdown, profit factor, net return, worst year, losing streak, recovery, robustness.
export function HeadlineStats({ s }: { s: BacktestSummary }) {
  const rob = s.robustness;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Stat label="Drawdown max" value={pct(s.max_drawdown)} hint={`SPY : ${pct(s.benchmark_drawdown)}`} />
      <Stat
        label="Profit factor"
        value={factor(s.profit_factor)}
        valueClass={s.profit_factor != null && s.profit_factor < 1 ? "text-danger" : ""}
        hint="Gains bruts / pertes brutes, sous 1 = perdant"
      />
      <Stat
        label="Rendement net"
        value={pct(s.net_return)}
        valueClass={pnlClass(s.net_return)}
        hint={`${pct(s.cagr)} par an (SPY : ${pct(s.benchmark_cagr)})`}
      />
      <Stat
        label="Pire année"
        value={s.worst_year ? pct(s.worst_year.return) : "—"}
        valueClass={pnlClass(s.worst_year?.return)}
        hint={s.worst_year ? String(s.worst_year.year) : undefined}
      />
      <Stat label="Pertes consécutives" value={String(s.max_consecutive_losses ?? "—")} hint="Plus longue série" />
      <Stat
        label="Temps de récupération"
        value={recoveryText(s.recovery_days, s.recovered)}
        hint="Du plus haut au retour à ce niveau"
      />
      <Stat
        label="Robustesse"
        value={rob ? ROBUSTNESS_LABEL[rob.label] : "—"}
        valueClass={rob ? ROBUSTNESS_CLASS[rob.label] : ""}
        hint={
          rob && rob.positive_windows != null
            ? `${pct(rob.positive_windows)} des ${rob.windows} fenêtres de 12 mois positives, hors échantillon ${
                rob.out_of_sample_positive ? "positif" : "négatif"
              }`
            : undefined
        }
      />
      <Stat
        label="Trades clôturés"
        value={String(s.trades)}
        hint={`Gagnants ${pct(s.win_rate)}, ${s.open_at_end} ouvert(s) à la fin${
          s.blocked_days ? `, ${s.blocked_days} j bloqués par les limites de pertes` : ""
        }`}
      />
    </div>
  );
}

const SCENARIO_ORDER = ["realiste", "pessimiste", "optimiste"];

export function ScenariosTable({ scenarios }: { scenarios: Record<string, ScenarioFigures> }) {
  const keys = [
    ...SCENARIO_ORDER.filter((k) => k in scenarios),
    ...Object.keys(scenarios).filter((k) => !SCENARIO_ORDER.includes(k)),
  ];
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold">Scénarios d&apos;exécution</h2>
      <p className="text-xs opacity-70">
        Le scénario réaliste est celui affiché partout ailleurs. Optimiste : vente au mid. Pessimiste : glissements et
        écarts bid/ask plus larges.
      </p>
      <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className={head}>
            <tr>
              <th className="px-2 py-1.5">Scénario</th>
              <th className="px-2 py-1.5 text-right">Drawdown</th>
              <th className="px-2 py-1.5 text-right">Profit factor</th>
              <th className="px-2 py-1.5 text-right">Net</th>
              <th className="px-2 py-1.5 text-right">Par an</th>
              <th className="px-2 py-1.5 text-right">Pire année</th>
              <th className="px-2 py-1.5 text-right">Robustesse</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k, i) => {
              const f = scenarios[k];
              return (
                <tr key={k} className={`border-t border-line/70 ${i === 0 ? "font-medium" : ""}`}>
                  <td className="px-2 py-1">{f.label}</td>
                  <td className={cell}>{pct(f.max_drawdown)}</td>
                  <td className={cell}>{factor(f.profit_factor)}</td>
                  <td className={`${cell} ${pnlClass(f.net_return)}`}>{pct(f.net_return)}</td>
                  <td className={`${cell} ${pnlClass(f.cagr)}`}>{pct(f.cagr)}</td>
                  <td className={`${cell} ${pnlClass(f.worst_year?.return)}`}>
                    {f.worst_year ? `${pct(f.worst_year.return)} (${f.worst_year.year})` : "—"}
                  </td>
                  <td className="px-2 py-1 text-right">{f.robustness ? ROBUSTNESS_LABEL[f.robustness.label] : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PeriodRow({ label, p }: { label: string; p: PeriodFigures | undefined }) {
  return (
    <tr className="border-t border-line/70">
      <td className="px-2 py-1">
        {label}
        {p ? <span className="block text-xs opacity-60">{`${day(p.start)} au ${day(p.end)}`}</span> : null}
      </td>
      <td className={cell}>{pct(p?.max_drawdown)}</td>
      <td className={cell}>{factor(p?.profit_factor)}</td>
      <td className={`${cell} ${pnlClass(p?.net_return)}`}>{pct(p?.net_return)}</td>
      <td className={cell}>{p?.trades ?? "—"}</td>
    </tr>
  );
}

export function PeriodsSection({ r }: { r: BacktestResult }) {
  const rolling = r.rolling ?? [];
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold">Calibration, hors échantillon et fenêtres glissantes</h2>
      <p className="text-xs opacity-70">
        Les 60 premiers pour cent de la période servent de calibration, le reste n&apos;a pas servi à choisir les
        réglages. Les réglages ne sont pas réoptimisés d&apos;une fenêtre à l&apos;autre : on mesure leur tenue dans le
        temps.
      </p>
      <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className={head}>
            <tr>
              <th className="px-2 py-1.5">Période</th>
              <th className="px-2 py-1.5 text-right">Drawdown</th>
              <th className="px-2 py-1.5 text-right">Profit factor</th>
              <th className="px-2 py-1.5 text-right">Net</th>
              <th className="px-2 py-1.5 text-right">Trades</th>
            </tr>
          </thead>
          <tbody>
            <PeriodRow label="Calibration" p={r.periods?.calibration} />
            <PeriodRow label="Hors échantillon" p={r.periods?.out_of_sample} />
          </tbody>
        </table>
      </div>
      {rolling.length ? (
        <details>
          <summary className="cursor-pointer text-sm">
            Fenêtres de 12 mois ({rolling.length}, décalées de 3 mois)
          </summary>
          <div className="mt-2 overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full text-sm">
              <tbody>
                {rolling.map((w) => (
                  <tr key={w.start} className="border-t border-line/70 first:border-0">
                    <td className="px-2 py-1 whitespace-nowrap">
                      {day(w.start)} au {day(w.end)}
                    </td>
                    <td className={`${cell} ${pnlClass(w.return)}`}>{pct(w.return)}</td>
                    <td className={cell}>DD {pct(w.max_drawdown)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  );
}

export function StressTable({ rows }: { rows: StressRow[] }) {
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold">Stress tests</h2>
      <p className="text-xs opacity-70">
        Les trades clôturés rejoués avec des conditions plus dures (premier ordre : mêmes entrées et mêmes sorties).
      </p>
      <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className={head}>
            <tr>
              <th className="px-2 py-1.5">Hypothèse</th>
              <th className="px-2 py-1.5 text-right">Drawdown</th>
              <th className="px-2 py-1.5 text-right">Profit factor</th>
              <th className="px-2 py-1.5 text-right">Net</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-line/70">
                <td className="px-2 py-1">
                  {row.label}
                  {row.loss != null ? (
                    <span className="block text-xs opacity-60">
                      {`Perte ${signed(-Math.abs(row.loss))} (${pct(row.loss_pct)})${row.date ? ` le ${day(row.date)}` : ""}`}
                    </span>
                  ) : null}
                </td>
                <td className={cell}>{pct(row.max_drawdown)}</td>
                <td className={cell}>{factor(row.profit_factor)}</td>
                <td className={`${cell} ${pnlClass(row.net_return)}`}>{pct(row.net_return)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function WheelSection({ w }: { w: WheelFigures }) {
  const parts: [string, number][] = [
    ["Puts vendus", w.puts_pnl],
    ["Covered calls", w.calls_pnl],
    ["Actions (plus ou moins-values)", w.shares_pnl],
  ];
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold">True Wheel complète</h2>
      <p className="text-xs opacity-70">
        {w.puts} put(s) vendu(s), {w.assignments} assignation(s), {w.covered_calls} covered call(s), {w.called_away}{" "}
        lot(s) appelé(s), {w.lots_open_at_end} lot(s) d&apos;actions encore détenu(s) à la fin (valorisés au dernier
        cours).
      </p>
      <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
        <table className="w-full text-sm">
          <tbody>
            {parts.map(([label, v]) => (
              <tr key={label} className="border-t border-line/70 first:border-0">
                <td className="px-2 py-1">{label}</td>
                <td className={`${cell} ${pnlClass(v)}`}>{signed(v)}</td>
              </tr>
            ))}
            <tr className="border-t border-line font-medium">
              <td className="px-2 py-1">Total de la Wheel</td>
              <td className={`${cell} ${pnlClass(w.total_pnl)}`}>{signed(w.total_pnl)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
