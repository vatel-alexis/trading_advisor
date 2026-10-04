import type { CurvePoint, MonthRow } from "@/lib/api";
import { day, month, signed, usd } from "@/lib/format";

// Hand-drawn SVG charts, rendered on the server: no chart library to ship. Hover shows the
// native tooltip of each point or bar; the tables under the charts carry the same numbers.

const W = 720;
const H = 240;
const PAD = { top: 12, right: 16, bottom: 28, left: 72 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

const DAY_MS = 86_400_000;
const time = (iso: string) => new Date(`${iso}T12:00:00`).getTime();

// Round tick values covering [min, max].
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

const axisLabel = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export function PnlCurve({ points, start }: { points: CurvePoint[]; start: number }) {
  if (points.length === 0) {
    return <EmptyChart text="La courbe apparaîtra à la première position terminée." />;
  }
  // The curve starts from the starting capital, the day before the first close.
  const series = [{ date: "", t: time(points[0].date) - DAY_MS, equity: start, point: null as CurvePoint | null }].concat(
    points.map((p) => ({ date: p.date, t: time(p.date), equity: p.equity, point: p })),
  );
  const yTicks = ticks(Math.min(start, ...series.map((s) => s.equity)), Math.max(start, ...series.map((s) => s.equity)));
  const yMin = yTicks[0];
  const yMax = yTicks[yTicks.length - 1];
  const tMin = series[0].t;
  const tMax = Math.max(series[series.length - 1].t, tMin + DAY_MS);
  const x = (t: number) => PAD.left + ((t - tMin) / (tMax - tMin)) * PLOT_W;
  const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin || 1)) * PLOT_H;
  const path = series.map((s, i) => `${i ? "L" : "M"}${x(s.t).toFixed(1)},${y(s.equity).toFixed(1)}`).join("");
  const xLabels = [series[0], series[Math.floor(series.length / 2)], series[series.length - 1]].filter(
    (s, i, all) => all.findIndex((o) => o.t === s.t) === i,
  );

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Capital réalisé au fil du temps">
      {yTicks.map((v) => (
        <g key={v}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" />
          <text x={PAD.left - 8} y={y(v)} dy="0.32em" textAnchor="end" className="fill-current text-[11px] opacity-60">
            {axisLabel.format(v)}
          </text>
        </g>
      ))}
      <line
        x1={PAD.left}
        x2={W - PAD.right}
        y1={y(start)}
        y2={y(start)}
        stroke="var(--chart-axis)"
        strokeDasharray="4 4"
      />
      {xLabels.map((s, i) => (
        <text
          key={s.t}
          x={x(s.t)}
          y={H - 8}
          textAnchor={i === 0 ? "start" : i === xLabels.length - 1 ? "end" : "middle"}
          className="fill-current text-[11px] opacity-60"
        >
          {s.point ? day(s.date) : "Départ"}
        </text>
      ))}
      <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" />
      {series.map((s) => (
        <g key={s.t}>
          <circle cx={x(s.t)} cy={y(s.equity)} r={series.length > 40 ? 0 : 4} fill="var(--series-1)" />
          {/* Larger invisible hit target for the tooltip. */}
          <circle cx={x(s.t)} cy={y(s.equity)} r={10} fill="transparent">
            <title>
              {s.point
                ? `${day(s.date)} : ${usd(s.equity)} (jour ${signed(s.point.pnl)}, cumul ${signed(s.point.cumulative)})`
                : `Capital de départ : ${usd(start)}`}
            </title>
          </circle>
        </g>
      ))}
    </svg>
  );
}

export function PremiumBars({ months }: { months: MonthRow[] }) {
  const rows = months.slice(-12);
  if (rows.length === 0) {
    return <EmptyChart text="Aucune prime encaissée pour l'instant." />;
  }
  const yTicks = ticks(0, Math.max(...rows.map((m) => m.premium), 1));
  const yMax = yTicks[yTicks.length - 1];
  const y = (v: number) => PAD.top + (1 - v / yMax) * PLOT_H;
  const slot = PLOT_W / rows.length;
  const barW = Math.min(slot - 2, 48);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Primes encaissées par mois">
      {yTicks.map((v) => (
        <g key={v}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" />
          <text x={PAD.left - 8} y={y(v)} dy="0.32em" textAnchor="end" className="fill-current text-[11px] opacity-60">
            {axisLabel.format(v)}
          </text>
        </g>
      ))}
      {rows.map((m, i) => {
        const cx = PAD.left + slot * (i + 0.5);
        const top = y(m.premium);
        const height = PAD.top + PLOT_H - top;
        // Rounded at the data end only, anchored square on the baseline.
        const r = Math.min(4, height, barW / 2);
        const left = cx - barW / 2;
        const right = cx + barW / 2;
        const base = PAD.top + PLOT_H;
        const d =
          height > 0
            ? `M${left},${base}V${top + r}Q${left},${top} ${left + r},${top}H${right - r}Q${right},${top} ${right},${top + r}V${base}Z`
            : "";
        return (
          <g key={m.month}>
            {d ? <path d={d} fill="var(--series-1)" /> : null}
            <rect x={cx - slot / 2} y={PAD.top} width={slot} height={PLOT_H} fill="transparent">
              <title>{`${month(m.month)} : ${usd(m.premium)} de primes, P&L réalisé ${signed(m.realized_pnl)}`}</title>
            </rect>
            <text x={cx} y={H - 8} textAnchor="middle" className="fill-current text-[11px] opacity-60">
              {month(m.month)}
            </text>
          </g>
        );
      })}
      <line x1={PAD.left} x2={W - PAD.right} y1={PAD.top + PLOT_H} y2={PAD.top + PLOT_H} stroke="var(--chart-axis)" />
    </svg>
  );
}

function EmptyChart({ text }: { text: string }) {
  return <p className="flex h-40 items-center justify-center text-sm opacity-60">{text}</p>;
}
