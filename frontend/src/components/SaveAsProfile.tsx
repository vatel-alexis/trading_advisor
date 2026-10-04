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
        className="rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/20"
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
        className="rounded border border-black/20 px-3 py-1 font-medium hover:bg-black/5 disabled:opacity-40 dark:border-white/20 dark:hover:bg-white/10"
      >
        Enregistrer ces réglages comme profil
      </button>
      {error ? <span className="text-xs text-red-600">{error}</span> : null}
    </div>
  );
}
