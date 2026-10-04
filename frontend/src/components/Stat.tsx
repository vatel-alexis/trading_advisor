export function Stat({
  label,
  value,
  hint,
  valueClass = "",
}: {
  label: string;
  value: string;
  hint?: string;
  valueClass?: string;
}) {
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface p-3 sm:p-4">
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted sm:text-[11px]">{label}</div>
      <div className={`mt-1 font-display text-lg font-bold break-words tabular-nums sm:text-xl ${valueClass}`}>{value}</div>
      {hint ? <div className="mt-1 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}

export function ApiDown() {
  return (
    <p className="rounded-2xl border border-danger/40 bg-danger/10 p-4 text-sm text-danger">
      API injoignable : vérifie que le backend tourne.
    </p>
  );
}
