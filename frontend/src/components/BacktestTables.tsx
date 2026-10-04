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
    <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/10">
      <table className="w-full text-sm">
        <thead className="bg-black/5 text-left text-xs uppercase tracking-wide opacity-70 dark:bg-white/5">
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
            <tr key={r.key} className="border-t border-black/5 dark:border-white/5">
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
