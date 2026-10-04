"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { createProfile } from "@/app/actions";
import type { Params } from "@/lib/api";

// Turns the parameters of a backtest (for instance an unsaved draft) into a saved profile.
export function SaveAsProfile({ params, suggestion }: { params: Params; suggestion: string }) {
  const router = useRouter();
  const [name, setName] = useState(suggestion);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={80}
        className="rounded-lg border border-line-strong bg-background px-2.5 py-1.5"
      />
      <button
        disabled={pending || !name.trim()}
        onClick={() =>
          startTransition(async () => {
            const r = await createProfile(name.trim(), "Créé depuis un backtest.", params);
            if (r.ok) router.push(`/reglages?profil=${r.id}`);
            else setError(r.message);
          })
        }
        className="rounded-full border border-line-strong px-3 hover:border-accent py-1 font-medium hover:bg-surface-2 disabled:opacity-40"
      >
        Enregistrer ces réglages comme profil
      </button>
      {error ? <span className="text-xs text-danger">{error}</span> : null}
    </div>
  );
}
