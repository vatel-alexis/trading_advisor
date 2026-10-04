import Link from "next/link";

import { ApiDown } from "@/components/Stat";
import { getHistory, type HistoryKind } from "@/lib/api";
import { REASON_LABEL, STRATEGY_LABEL, day, pnlClass, price, signed, strikes } from "@/lib/format";

export const dynamic = "force-dynamic";

const KINDS: { value: HistoryKind; label: string }[] = [
  { value: "closed", label: "Fermée" },
  { value: "expired", label: "Expirée" },
  { value: "assigned", label: "Assignée" },
  { value: "canceled", label: "Non exécutée" },
  { value: "rejected", label: "Rejetée" },
  { value: "ignored", label: "Non traitée" },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label])) as Record<HistoryKind, string>;

type Search = Record<string, string | string[] | undefined>;

const one = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value) ?? "";
const many = (value: string | string[] | undefined) => (Array.isArray(value) ? value : value ? [value] : []);

export default async function Page({ searchParams }: { searchParams: Promise<Search> }) {
  const params = await searchParams;
  const kinds = many(params.kind).filter((k): k is HistoryKind => KINDS.some((x) => x.value === k));
  const underlying = one(params.underlying);
  const strategy = one(params.strategy);
  const since = one(params.since);
  const until = one(params.until);

  const query = new URLSearchParams();
  kinds.forEach((k) => query.append("kind", k));
  if (underlying) query.set("underlying", underlying);
  if (strategy) query.set("strategy", strategy);
  if (since) query.set("since", since);
  if (until) query.set("until", until);
  const data = await getHistory(query);

  const realized = data?.rows.filter((r) => r.kind !== "rejected" && r.kind !== "ignored") ?? [];
  const total = realized.reduce((sum, r) => sum + (r.pnl ?? 0), 0);
  const field = "rounded-lg border border-line-strong bg-background px-2.5 py-1.5";

  return (
    <section className="space-y-4">
      <div>
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Historique</h1>
        <p className="text-sm opacity-70">
          Positions terminées et deals rejetés ou non traités, gardés pour mesurer les biais de sélection.
        </p>
      </div>

      <form
        method="get"
        className="flex flex-wrap items-end gap-3 rounded-2xl border border-line bg-surface p-3 text-sm"
      >
        <fieldset className="flex basis-full flex-wrap gap-x-3 gap-y-1 sm:basis-auto">
          <legend className="mb-1 text-xs opacity-60">Statut</legend>
          {KINDS.map((k) => (
            <label key={k.value} className="flex items-center gap-1">
              <input type="checkbox" name="kind" value={k.value} defaultChecked={kinds.includes(k.value)} />
              {k.label}
            </label>
          ))}
        </fieldset>
        <label className="flex min-w-[40%] flex-1 flex-col gap-1 sm:min-w-0 sm:flex-none">
          <span className="text-xs opacity-60">Titre</span>
          <select name="underlying" defaultValue={underlying} className={field}>
            <option value="">Tous</option>
            {(data?.underlyings ?? []).map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-[40%] flex-1 flex-col gap-1 sm:min-w-0 sm:flex-none">
          <span className="text-xs opacity-60">Stratégie</span>
          <select name="strategy" defaultValue={strategy} className={field}>
            <option value="">Toutes</option>
            {Object.entries(STRATEGY_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-[40%] flex-1 flex-col gap-1 sm:min-w-0 sm:flex-none">
          <span className="text-xs opacity-60">Du</span>
          <input type="date" name="since" defaultValue={since} className={field} />
        </label>
        <label className="flex min-w-[40%] flex-1 flex-col gap-1 sm:min-w-0 sm:flex-none">
          <span className="text-xs opacity-60">Au</span>
          <input type="date" name="until" defaultValue={until} className={field} />
        </label>
        <button className="rounded-full bg-grad px-4 py-1.5 font-medium text-on-accent">Filtrer</button>
        <Link href="/historique" className="py-1.5 text-accent underline decoration-accent/40 underline-offset-4 opacity-70">
          Réinitialiser
        </Link>
      </form>

      {data === null ? (
        <ApiDown />
      ) : data.rows.length === 0 ? (
        <p className="rounded-2xl border border-line bg-surface p-4 text-sm opacity-70">
          Aucune ligne pour ces filtres.
        </p>
      ) : (
        <>
          <p className="text-sm">
            {data.rows.length} ligne(s) · P&amp;L réalisé des positions affichées :{" "}
            <span className={`font-medium tabular-nums ${pnlClass(total)}`}>{signed(total)}</span>
          </p>
          {/* Phones: one card per line, the table needs a wider screen. */}
          <div className="space-y-3 md:hidden">
            {data.rows.map((r) => (
              <article key={r.id} className="rounded-2xl border border-line bg-surface p-4 text-[13px]">
                <header className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-display text-base font-bold">{r.underlying}</div>
                    <div className="text-xs text-muted">
                      {STRATEGY_LABEL[r.strategy]} · {KIND_LABEL[r.kind]}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`font-display font-bold tabular-nums ${pnlClass(r.pnl)}`}>{signed(r.pnl)}</div>
                    <div className="text-xs text-muted">{day(r.date)}</div>
                  </div>
                </header>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                  <span className="font-mono">{strikes(r.strikes)}</span>
                  <span>Éch. {day(r.expiration)}</span>
                  <span className="tabular-nums">
                    Qté {r.quantity} · crédit {price(r.credit)} · rachat {price(r.debit)}
                  </span>
                </div>
                {r.reason || r.note ? (
                  <div className="mt-2 text-xs">
                    {r.reason ? (REASON_LABEL[r.reason] ?? r.reason) : null}
                    {r.note ? <div className="text-muted">{r.note}</div> : null}
                  </div>
                ) : null}
              </article>
            ))}
          </div>
          <div className="hidden overflow-x-auto rounded-2xl border border-line bg-surface md:block">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
                <tr>
                  <th className="px-3 py-2">Date</th>
                  <th className="px-3 py-2">Statut</th>
                  <th className="px-3 py-2">Titre</th>
                  <th className="px-3 py-2">Strikes</th>
                  <th className="px-3 py-2">Échéance</th>
                  <th className="px-3 py-2 text-right">Qté</th>
                  <th className="px-3 py-2 text-right">Crédit</th>
                  <th className="px-3 py-2 text-right">Rachat</th>
                  <th className="px-3 py-2 text-right">P&amp;L</th>
                  <th className="px-3 py-2">Motif</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.id} className="border-t border-line/70">
                    <td className="px-3 py-2 whitespace-nowrap">{day(r.date)}</td>
                    <td className="px-3 py-2">{KIND_LABEL[r.kind]}</td>
                    <td className="px-3 py-2">
                      <div className="font-medium">{r.underlying}</div>
                      <div className="text-xs opacity-60">{STRATEGY_LABEL[r.strategy]}</div>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{strikes(r.strikes)}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{day(r.expiration)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.quantity}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{price(r.credit)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{price(r.debit)}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${pnlClass(r.pnl)}`}>{signed(r.pnl)}</td>
                    <td className="px-3 py-2 text-xs">
                      {r.reason ? (REASON_LABEL[r.reason] ?? r.reason) : "—"}
                      {r.note ? <div className="opacity-60">{r.note}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
