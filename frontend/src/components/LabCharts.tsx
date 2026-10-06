import { day, usd } from "@/lib/format";

// Account value over time for one or several backtests, hand-drawn like the dashboard charts.

const W = 720;
const H = 260;
const PAD = { top: 12, right: 16, bottom: 28, left: 72 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

export const SERIES_COLORS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
];

export type Line = { label: string; color: string; dashed?: boolean; points: { date: string; value: number }[] };

const time = (iso: string) => new Date(`${iso}T12:00:00`).getTime();
const axisFormats = {
  USD: new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD", maximumFractionDigits: 0 }),
  EUR: new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }),
};

function ticks(min: number, max: number, count = 4): number[] {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const first = Math.floor(min / step) * step;
  const values = [];
  for (let v = first; v <= max + step * 0.5; v += step) values.push(Math.round(v * 100) / 100);
  return values;
}

export function EquityLines({
  lines,
  capital,
  currency = "USD",
}: {
  lines: Line[];
  capital: number;
  currency?: "USD" | "EUR";
}) {
  const axisLabel = axisFormats[currency];
  const all = lines.flatMap((l) => l.points);
  if (all.length < 2) return <p className="flex h-40 items-center justify-center text-sm opacity-60">Pas de courbe.</p>;
  const values = all.map((p) => p.value);
  const yTicks = ticks(Math.min(capital, ...values), Math.max(capital, ...values));
  const yMin = yTicks[0];
  const yMax = yTicks[yTicks.length - 1];
  const times = all.map((p) => time(p.date));
  const tMin = Math.min(...times);
  const tMax = Math.max(...times, tMin + 1);
  const x = (t: number) => PAD.left + ((t - tMin) / (tMax - tMin)) * PLOT_W;
  const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin || 1)) * PLOT_H;
  const years: number[] = [];
  for (let year = new Date(tMin).getFullYear() + 1; year <= new Date(tMax).getFullYear(); year++) years.push(year);
  const step = Math.ceil(years.length / 8) || 1;

  return (
    <div className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Valeur du compte au fil du temps">
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" />
            <text
              x={PAD.left - 8}
              y={y(v)}
              dy="0.32em"
              textAnchor="end"
              className="fill-current text-[11px] opacity-60"
            >
              {axisLabel.format(v)}
            </text>
          </g>
        ))}
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={y(capital)}
          y2={y(capital)}
          stroke="var(--chart-axis)"
          strokeDasharray="4 4"
        />
        {years
          .filter((_, i) => i % step === 0)
          .map((year) => {
            const t = new Date(`${year}-01-01T12:00:00`).getTime();
            return (
              <text key={year} x={x(t)} y={H - 8} textAnchor="middle" className="fill-current text-[11px] opacity-60">
                {year}
              </text>
            );
          })}
        {lines.map((l) => (
          <path
            key={l.label}
            d={l.points
              .map((p, i) => `${i ? "L" : "M"}${x(time(p.date)).toFixed(1)},${y(p.value).toFixed(1)}`)
              .join("")}
            fill="none"
            stroke={l.color}
            strokeWidth={l.dashed ? 1.5 : 2}
            strokeDasharray={l.dashed ? "5 4" : undefined}
            strokeLinejoin="round"
          >
            <title>
              {`${l.label} : ${currency === "USD" ? usd(l.points[l.points.length - 1]?.value) : axisLabel.format(l.points[l.points.length - 1]?.value ?? 0)} au ${day(l.points[l.points.length - 1]?.date)}`}
            </title>
          </path>
        ))}
      </svg>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {lines.map((l) => (
          <li key={l.label} className="flex items-center gap-1.5">
            <svg width="18" height="6" aria-hidden>
              <line
                x1="0"
                x2="18"
                y1="3"
                y2="3"
                stroke={l.color}
                strokeWidth="2"
                strokeDasharray={l.dashed ? "4 3" : undefined}
              />
            </svg>
            {l.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
