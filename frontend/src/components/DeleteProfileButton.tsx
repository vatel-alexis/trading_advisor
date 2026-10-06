"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { deleteProfile } from "@/app/actions";

export const DELETE_CONFIRM = (name: string) =>
  `Supprimer définitivement le profil « ${name} » ? Ses backtests restent consultables sous ce nom.`;

// Delete button of a profile card in the settings list (the active profile has none).
export function DeleteProfileButton({ id, name, selected }: { id: number; name: string; selected: boolean }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button
      type="button"
      disabled={pending}
      aria-label={`Supprimer le profil ${name}`}
      title="Supprimer ce profil"
      onClick={() => {
        if (!window.confirm(DELETE_CONFIRM(name))) return;
        startTransition(async () => {
          const r = await deleteProfile(id);
          if (!r.ok) window.alert(r.message);
          else if (selected) router.push("/reglages");
          else router.refresh();
        });
      }}
      className="rounded-full px-2 py-0.5 text-xs text-danger opacity-60 hover:bg-danger/10 hover:opacity-100 disabled:opacity-30"
    >
      {pending ? "…" : "Supprimer"}
    </button>
  );
}
