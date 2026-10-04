"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { deleteBacktest, launchBacktest } from "@/app/actions";
import type { ActionResult, Backtests, Profile } from "@/lib/api";

const INPUT = "rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/20";

export function BacktestLauncher({
  profiles,
  defaults,
  modelFields,
  modelDefaults,
  hasCache,
}: {
  profiles: Profile[];
  defaults: Backtests["defaults"];
  modelFields: Backtests["model_fields"];
  modelDefaults: Record<string, number>;
  hasCache: boolean;
}) {
  const router = useRouter();
  const active = profiles.find((p) => p.is_active) ?? profiles[0];
  const [profileId, setProfileId] = useState<number | null>(active?.id ?? null);
  const [start, setStart] = useState(defaults.start);
  const [end, setEnd] = useState(defaults.end);
  const [capital, setCapital] = useState(String(defaults.capital));
  const [refresh, setRefresh] = useState(false);
  const [model, setModel] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(modelDefaults).map(([k, v]) => [k, String(v)])),
  );
  const [result, setResult] = useState<ActionResult | null>(null);
  const [pending, startTransition] = useTransition();

  function submit() {
    // Only the assumptions changed from the calibrated defaults are sent.
    const overrides = Object.fromEntries(
      Object.entries(model)
        .map(([k, v]) => [k, Number(v.replace(",", "."))] as const)
        .filter(([k, v]) => v !== modelDefaults[k]),
    );
    startTransition(async () => {
      const r = await launchBacktest({
        profile_id: profileId,
        start,
        end,
        capital: Number(capital),
        model: overrides,
        refresh_data: refresh,
      });
      setResult(r);
      if (r.ok) router.refresh();
    });
  }

  return (
    <div className="space-y-3 rounded-lg border border-black/10 p-4 text-sm dark:border-white/10">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs opacity-60">Profil</span>
          <select value={profileId ?? ""} onChange={(e) => setProfileId(Number(e.target.value))} className={INPUT}>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.is_active ? " (actif)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs opacity-60">Du</span>
          <input
            type="date"
            value={start}
            min="2019-01-02"
            onChange={(e) => setStart(e.target.value)}
            className={INPUT}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs opacity-60">Au</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={INPUT} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs opacity-60">Capital ($)</span>
          <input
            type="number"
            value={capital}
            min={1000}
            step={1000}
            onChange={(e) => setCapital(e.target.value)}
            className={`${INPUT} w-28`}
          />
        </label>
        <label className="flex items-center gap-1 pb-1.5">
          <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} />
          Retélécharger l&apos;historique
        </label>
        <button
          disabled={pending || profileId == null}
          onClick={submit}
          className="rounded bg-foreground px-3 py-1.5 font-medium text-background disabled:opacity-40"
        >
          {pending ? "Envoi…" : "Lancer le backtest"}
        </button>
      </div>
      <details>
        <summary className="cursor-pointer text-xs opacity-70">Hypothèses de reconstruction des prix (avancé)</summary>
        <p className="mt-2 text-xs opacity-60">
          Les prix d&apos;options sont reconstruits par Black-Scholes : ces hypothèses déplacent les résultats. Les
          valeurs proposées sont celles calibrées sur les chaînes Yahoo du 2 octobre 2026.
        </p>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {modelFields.map((f) => (
            <label key={f.key} className="flex items-center justify-between gap-2">
              <span className="text-xs">{f.label}</span>
              <input
                type="number"
                step="any"
                min={0}
                value={model[f.key] ?? ""}
                onChange={(e) => setModel((m) => ({ ...m, [f.key]: e.target.value }))}
                className={`${INPUT} w-24 tabular-nums`}
              />
            </label>
          ))}
        </div>
      </details>
      {!hasCache ? (
        <p className="text-xs opacity-60">
          Pas encore d&apos;historique en base : le premier backtest le télécharge depuis Yahoo (quelques minutes).
        </p>
      ) : null}
      {result ? (
        <p className={result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600"}>{result.message}</p>
      ) : null}
    </div>
  );
}

export function DeleteRunButton({ id }: { id: number }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="flex flex-col items-end">
      <button
        type="button"
        disabled={pending}
        onClick={() => {
          if (!window.confirm(`Supprimer le backtest n° ${id} ?`)) return;
          startTransition(async () => {
            const r = await deleteBacktest(id);
            if (r.ok) router.refresh();
            else setError(r.message);
          });
        }}
        className="text-xs underline underline-offset-4 opacity-60 hover:opacity-100 disabled:opacity-30"
      >
        Supprimer
      </button>
      {error ? <span className="text-xs text-red-600">{error}</span> : null}
    </span>
  );
}
