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
  const field = "rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/20";

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Historique</h1>
        <p className="text-sm opacity-70">
          Positions terminées et deals rejetés ou non traités, gardés pour mesurer les biais de sélection.
        </p>
      </div>

      <form
        method="get"
        className="flex flex-wrap items-end gap-3 rounded-lg border border-black/10 p-3 text-sm dark:border-white/10"
      >
        <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
          <legend className="mb-1 text-xs opacity-60">Statut</legend>
          {KINDS.map((k) => (
            <label key={k.value} className="flex items-center gap-1">
              <input type="checkbox" name="kind" value={k.value} defaultChecked={kinds.includes(k.value)} />
              {k.label}
            </label>
          ))}
        </fieldset>
        <label className="flex flex-col gap-1">
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
        <label className="flex flex-col gap-1">
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
        <label className="flex flex-col gap-1">
          <span className="text-xs opacity-60">Du</span>
          <input type="date" name="since" defaultValue={since} className={field} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs opacity-60">Au</span>
          <input type="date" name="until" defaultValue={until} className={field} />
        </label>
        <button className="rounded bg-foreground px-3 py-1.5 font-medium text-background">Filtrer</button>
        <Link href="/historique" className="py-1.5 underline underline-offset-4 opacity-70">
          Réinitialiser
        </Link>
      </form>

      {data === null ? (
        <ApiDown />
      ) : data.rows.length === 0 ? (
        <p className="rounded-lg border border-black/10 p-4 text-sm opacity-70 dark:border-white/10">
          Aucune ligne pour ces filtres.
        </p>
      ) : (
        <>
          <p className="text-sm">
            {data.rows.length} ligne(s) · P&amp;L réalisé des positions affichées :{" "}
            <span className={`font-medium tabular-nums ${pnlClass(total)}`}>{signed(total)}</span>
          </p>
          <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/10">
            <table className="w-full text-sm">
              <thead className="bg-black/5 text-left text-xs uppercase tracking-wide opacity-70 dark:bg-white/5">
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
                  <tr key={r.id} className="border-t border-black/5 dark:border-white/5">
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
