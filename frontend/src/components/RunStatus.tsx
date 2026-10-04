import type { BacktestRun } from "@/lib/api";

export function RunStatus({ run }: { run: BacktestRun }) {
  if (run.status === "done") return <span className="text-emerald-600 dark:text-emerald-400">Terminé</span>;
  if (run.status === "failed")
    return (
      <span className="text-red-600" title={run.error ?? undefined}>
        Échec
      </span>
    );
  return (
    <div className="w-28">
      <div className="text-xs opacity-70">{run.status === "queued" ? "En attente" : (run.step ?? "En cours")}</div>
      <div className="mt-1 h-1.5 overflow-hidden rounded bg-black/10 dark:bg-white/10">
        <div className="h-full bg-[var(--series-1)]" style={{ width: `${Math.round(run.progress * 100)}%` }} />
      </div>
    </div>
  );
}
