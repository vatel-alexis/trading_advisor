import type { BreakdownRow } from "@/lib/api";
import { pct, pnlClass, signed } from "@/lib/format";

export function BreakdownTable({
  title,
  rows,
  label = (key) => key,
}: {
  title: string;
  rows: BreakdownRow[];
  label?: (key: string) => string;
}) {
  const td = "px-2 py-1 tabular-nums";
  return (
    <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
      <table className="w-full text-sm">
        <thead className="bg-surface-2 text-left font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
          <tr>
            <th className="px-2 py-1.5">{title}</th>
            <th className="px-2 py-1.5 text-right">Trades</th>
            <th className="px-2 py-1.5 text-right">Gagnants</th>
            <th className="px-2 py-1.5 text-right">P&amp;L total</th>
            <th className="px-2 py-1.5 text-right">P&amp;L moyen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-line/70">
              <td className="px-2 py-1">{label(r.key)}</td>
              <td className={`${td} text-right`}>{r.trades}</td>
              <td className={`${td} text-right`}>{pct(r.win_rate)}</td>
              <td className={`${td} text-right ${pnlClass(r.pnl)}`}>{signed(r.pnl)}</td>
              <td className={`${td} text-right ${pnlClass(r.avg)}`}>{signed(r.avg)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
