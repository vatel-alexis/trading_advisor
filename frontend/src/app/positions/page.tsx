import { AutoRefresh } from "@/components/AutoRefresh";
import { CloseButton } from "@/components/CloseButton";
import { ApiDown } from "@/components/Stat";
import { getPositions, type OptionPosition } from "@/lib/api";
import { STRATEGY_LABEL, dateTime, day, pct, pnlClass, price, signed, strikes, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

const EXIT_LABEL: Record<string, string> = {
  stop_loss: "Stop en cours",
  time_exit: "Sortie 21 j en cours",
  manual_close: "Rachat en cours",
};

function State({ p }: { p: OptionPosition }) {
  if (p.status === "pending") {
    return <span className="text-amber-600">Ordre d&apos;ouverture à {price(p.open_limit)}</span>;
  }
  if (p.exit_order) {
    return (
      <span className="text-amber-600">
        {EXIT_LABEL[p.exit_order.purpose] ?? p.exit_order.purpose} à {price(p.exit_order.limit)}
      </span>
    );
  }
  return <span className="opacity-70">TP à {price(p.take_profit_price)}</span>;
}

export default async function Page() {
  const data = await getPositions();

  return (
    <section className="space-y-4">
      <AutoRefresh seconds={30} />
      <div>
        <h1 className="text-2xl font-semibold">Positions ouvertes</h1>
        <p className="text-sm opacity-70">
          Mark au milieu bid/ask, relevé toutes les 5 minutes pendant la séance. Le rachat manuel annule l&apos;ordre de
          prise de profit puis rachète au prix naturel (marché ouvert uniquement).
        </p>
      </div>
      {data === null ? (
        <ApiDown />
      ) : (
        <>
          {data.options.length === 0 ? (
            <p className="rounded-lg border border-black/10 p-4 text-sm opacity-70 dark:border-white/10">
              Aucune position ouverte.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/10">
              <table className="w-full text-sm">
                <thead className="bg-black/5 text-left text-xs uppercase tracking-wide opacity-70 dark:bg-white/5">
                  <tr>
                    <th className="px-3 py-2">Titre</th>
                    <th className="px-3 py-2">Strikes</th>
                    <th className="px-3 py-2">Échéance</th>
                    <th className="px-3 py-2 text-right">Contrats</th>
                    <th className="px-3 py-2 text-right">Crédit</th>
                    <th className="px-3 py-2 text-right">Mark</th>
                    <th className="px-3 py-2 text-right">P&amp;L latent</th>
                    <th className="px-3 py-2 text-right">% du crédit</th>
                    <th className="px-3 py-2">État</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {data.options.map((p) => (
                    <tr key={p.id} className="border-t border-black/5 dark:border-white/5">
                      <td className="px-3 py-2">
                        <div className="font-medium">{p.underlying}</div>
                        <div className="text-xs opacity-60">{STRATEGY_LABEL[p.strategy]}</div>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{strikes(p.legs.map((l) => l.strike))}</td>
                      <td className="px-3 py-2">
                        {day(p.expiration)}
                        <div className="text-xs opacity-60">{p.dte != null ? `${p.dte} j` : ""}</div>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{p.contracts}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{price(p.entry_credit)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {price(p.mark)}
                        <div className="text-xs opacity-60">{p.marked_at ? dateTime(p.marked_at) : "pas encore"}</div>
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums ${pnlClass(p.unrealized_pnl)}`}>
                        {signed(p.unrealized_pnl)}
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums ${pnlClass(p.profit_pct)}`}>
                        {pct(p.profit_pct)}
                      </td>
                      <td className="px-3 py-2 text-xs">
                        <State p={p} />
                      </td>
                      <td className="px-3 py-2 text-right">{p.can_close ? <CloseButton id={p.id} /> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.share_lots.length > 0 ? (
            <div className="space-y-2">
              <h2 className="text-lg font-semibold">Actions détenues (wheel)</h2>
              <div className="overflow-x-auto rounded-lg border border-black/10 dark:border-white/10">
                <table className="w-full text-sm">
                  <thead className="bg-black/5 text-left text-xs uppercase tracking-wide opacity-70 dark:bg-white/5">
                    <tr>
                      <th className="px-3 py-2">Titre</th>
                      <th className="px-3 py-2">Depuis</th>
                      <th className="px-3 py-2 text-right">Actions</th>
                      <th className="px-3 py-2 text-right">Prix de revient</th>
                      <th className="px-3 py-2 text-right">Capital immobilisé</th>
                      <th className="px-3 py-2 text-right">Couvertes par un call</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.share_lots.map((lot) => (
                      <tr key={lot.id} className="border-t border-black/5 dark:border-white/5">
                        <td className="px-3 py-2 font-medium">{lot.underlying}</td>
                        <td className="px-3 py-2">{day(lot.opened_at)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{lot.shares}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{price(lot.cost_basis)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{usd(lot.collateral)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{lot.covered_shares}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
