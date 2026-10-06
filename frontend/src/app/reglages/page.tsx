import Link from "next/link";

import { DeleteProfileButton } from "@/components/DeleteProfileButton";
import { Glossary } from "@/components/Glossary";
import { ProfileEditor } from "@/components/ProfileEditor";
import { ApiDown } from "@/components/Stat";
import { getBacktests, getProfiles } from "@/lib/api";
import { pct } from "@/lib/format";

export const dynamic = "force-dynamic";

type Search = Record<string, string | string[] | undefined>;

export default async function Page({ searchParams }: { searchParams: Promise<Search> }) {
  const query = await searchParams;
  const [data, backtests] = await Promise.all([getProfiles(), getBacktests()]);
  if (!data) {
    return (
      <section className="space-y-4">
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Réglages</h1>
        <ApiDown />
      </section>
    );
  }

  const wanted = Number(Array.isArray(query.profil) ? query.profil[0] : query.profil);
  const profile =
    data.profiles.find((p) => p.id === wanted) ?? data.profiles.find((p) => p.is_active) ?? data.profiles[0];
  const labels = Object.fromEntries(data.fields.map((f) => [f.key, f.label]));
  // Latest finished backtest of each profile, shown next to its name.
  const lastRun = new Map<number, number | null>();
  for (const run of backtests?.runs ?? []) {
    if (run.profile_id != null && run.status === "done" && !lastRun.has(run.profile_id)) {
      lastRun.set(run.profile_id, run.summary?.cagr ?? null);
    }
  }

  return (
    <section className="space-y-4">
      <div>
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Réglages</h1>
        <p className="text-sm opacity-70">
          Un profil est un jeu de réglages. Le profil actif est celui du screener (version {data.active_version}) ; les
          autres servent aux backtests. L&apos;essentiel est en haut du formulaire, le reste dans « Réglages avancés ».
        </p>
      </div>

      <Glossary />

      <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
        <nav className="space-y-1 text-sm">
          {data.profiles.map((p) => (
            <div
              key={p.id}
              className={`rounded-2xl border ${
                p.id === profile?.id
                  ? "border-accent/60 bg-surface-2"
                  : "border-line bg-surface hover:bg-surface-2"
              }`}
            >
              <div className="flex items-start justify-between gap-2 p-3 pb-0">
                <Link href={`/reglages?profil=${p.id}`} className="min-w-0 flex-1 font-medium">
                  {p.name}
                </Link>
                {p.is_active ? (
                  <span className="rounded-full border border-success/40 bg-success/10 px-1.5 py-0.5 text-[10px] font-medium uppercase text-success">
                    Actif
                  </span>
                ) : (
                  <DeleteProfileButton id={p.id} name={p.name} selected={p.id === profile?.id} />
                )}
              </div>
              <Link href={`/reglages?profil=${p.id}`} className="block px-3 pb-3">
                <div className="mt-1 hidden text-xs opacity-60 md:block">
                  {p.changed.length === 0
                    ? "Valeurs par défaut"
                    : `${p.changed.length} réglage(s) modifié(s) : ${p.changed
                        .slice(0, 3)
                        .map((k) => labels[k] ?? k)
                        .join(", ")}${p.changed.length > 3 ? "…" : ""}`}
                </div>
                {lastRun.has(p.id) ? (
                  <div className="mt-1 text-xs opacity-60">Dernier backtest : {pct(lastRun.get(p.id))}/an</div>
                ) : null}
              </Link>
            </div>
          ))}
          <Link href="/backtests" className="block px-1 pt-2 text-xs text-accent underline decoration-accent/40 underline-offset-4 opacity-70">
            Voir et comparer les backtests
          </Link>
        </nav>

        {profile ? (
          <ProfileEditor
            key={`${profile.id}-${profile.updated_at}`}
            profile={profile}
            fields={data.fields}
            groups={data.groups}
            defaults={data.defaults}
            simpleGroups={data.simple_groups ?? []}
            riskLevels={data.risk_levels ?? []}
            backtestDefaults={
              backtests?.defaults ?? { start: "2019-01-02", end: new Date().toISOString().slice(0, 10), capital: 20000 }
            }
          />
        ) : null}
      </div>
    </section>
  );
}
