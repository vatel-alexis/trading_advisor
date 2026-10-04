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
    <div className="rounded-lg border border-black/10 p-4 dark:border-white/10">
      <div className="text-xs uppercase tracking-wide opacity-60">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${valueClass}`}>{value}</div>
      {hint ? <div className="mt-1 text-xs opacity-60">{hint}</div> : null}
    </div>
  );
}

export function ApiDown() {
  return (
    <p className="rounded-lg border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-600">
      API injoignable : vérifie que le backend tourne.
    </p>
  );
}
