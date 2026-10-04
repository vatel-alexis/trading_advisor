"use client";

import { useState, useTransition } from "react";

import { closePosition } from "@/app/actions";
import type { ActionResult } from "@/lib/api";
import { newKey } from "@/lib/format";

export function CloseButton({ id }: { id: number }) {
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<ActionResult | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  if (result?.ok) return <span className="text-xs text-emerald-600 dark:text-emerald-400">{result.message}</span>;

  function submit() {
    const k = key ?? newKey();
    setKey(k);
    startTransition(async () => {
      const r = await closePosition(id, k);
      setResult(r);
      if (!r.ok) {
        setConfirming(false);
        setKey(null); // refused before any order: the next click is a new attempt
      }
    });
  }

  return (
    <div className="flex flex-col items-end gap-1">
      {confirming ? (
        <div className="flex gap-1">
          <button
            disabled={pending}
            onClick={submit}
            className="rounded bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            {pending ? "Envoi…" : "Confirmer le rachat"}
          </button>
          <button
            disabled={pending}
            onClick={() => setConfirming(false)}
            className="rounded border border-black/20 px-2 py-1 text-xs dark:border-white/20"
          >
            Non
          </button>
        </div>
      ) : (
        <button
          onClick={() => setConfirming(true)}
          className="rounded border border-black/20 px-2 py-1 text-xs font-medium hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        >
          Racheter
        </button>
      )}
      {result && !result.ok ? <span className="max-w-48 text-right text-xs text-red-600">{result.message}</span> : null}
    </div>
  );
}
