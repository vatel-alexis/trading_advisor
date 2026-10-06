import { AutoRefresh } from "@/components/AutoRefresh";
import { CloseButton } from "@/components/CloseButton";
import { ExecutionDetails } from "@/components/ExecutionDetails";
import { ApiDown } from "@/components/Stat";
import { getPositions, type OptionPosition } from "@/lib/api";
import { STRATEGY_LABEL, dateTime, day, pct, pnlClass, price, signed, strikes, usd } from "@/lib/format";
import { Fragment } from "react";

export const dynamic = "force-dynamic";

const EXIT_LABEL: Record<string, string> = {
  stop_loss: "Stop en cours",
  time_exit: "Sortie anticipée en cours",
  manual_close: "Rachat en cours",
};

function Step({ step, steps }: { step: number | null | undefined; steps: number }) {
  if (step == null || steps <= 0) return null;
  return (
    <>
      {" "}
      (palier {Math.min(step, steps)}/{steps})
    </>
  );
}

function State({ p }: { p: OptionPosition }) {
  if (p.status === "pending") {
    return (
      <span className="text-warning">
        Ordre d&apos;ouverture à {price(p.open_limit)}
        <Step step={p.open_step} steps={p.execution.limit_steps} />
      </span>
    );
  }
  if (p.exit_order) {
    return (
      <span className="text-warning">
        {EXIT_LABEL[p.exit_order.purpose] ?? p.exit_order.purpose} à {price(p.exit_order.limit)}
        <Step step={p.exit_order.step} steps={p.execution.limit_steps} />
      </span>
    );
  }
  if (p.take_profit_price == null) return <span className="opacity-70">Sans objectif de gain</span>;
  return <span className="opacity-70">TP à {price(p.take_profit_price)}</span>;
}

export default async function Page() {
  const data = await getPositions();

  return (
    <section className="space-y-4">
      <AutoRefresh seconds={30} />
      <div>
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Positions ouvertes</h1>
        <p className="text-sm opacity-70">
          Mark au milieu bid/ask, relevé toutes les 5 minutes pendant la séance avec le prix naturel. Les sorties se
          calculent sur le crédit exécuté. Le stop se déclenche sur le prix de rachat attendu confirmé sur plusieurs
          relevés, pas sur le mid seul, et envoie un ordre limite : il ne garantit pas le prix d&apos;exécution. Le
          rachat manuel annule l&apos;ordre de prise de profit puis rachète au prix naturel (marché ouvert uniquement).
        </p>
      </div>
      {data === null ? (
        <ApiDown />
      ) : (
        <>
          {data.options.length === 0 ? (
            <p className="rounded-2xl border border-line bg-surface p-4 text-sm opacity-70">Aucune position ouverte.</p>
          ) : (
            <>
              {/* Phones: one card per position, the table needs a wider screen. */}
              <div className="space-y-3 md:hidden">
                {data.options.map((p) => (
                  <article key={p.id} className="rounded-2xl border border-line bg-surface p-4">
                    <header className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-display text-lg font-bold">{p.underlying}</div>
                        <div className="text-xs text-muted">{STRATEGY_LABEL[p.strategy]}</div>
                      </div>
                      <div className="text-right">
                        <div className={`font-display text-lg font-bold tabular-nums ${pnlClass(p.unrealized_pnl)}`}>
                          {signed(p.unrealized_pnl)}
                        </div>
                        <div className={`text-xs tabular-nums ${pnlClass(p.profit_pct)}`}>
                          {pct(p.profit_pct)} du crédit
                        </div>
                      </div>
                    </header>
                    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[13px]">
                      <div className="flex justify-between gap-2">
                        <dt className="text-muted">Strikes</dt>
                        <dd className="font-mono text-xs">{strikes(p.legs.map((l) => l.strike))}</dd>
                      </div>
                      <div className="flex justify-between gap-2">
                        <dt className="text-muted">Échéance</dt>
                        <dd className="tabular-nums">{p.dte != null ? `${p.dte} j` : day(p.expiration)}</dd>
                      </div>
                      <div className="flex justify-between gap-2">
                        <dt className="text-muted">Contrats</dt>
                        <dd className="tabular-nums">{p.contracts}</dd>
                      </div>
                      <div className="flex justify-between gap-2">
                        <dt className="text-muted">Crédit</dt>
                        <dd className="tabular-nums">{price(p.entry_credit)}</dd>
                      </div>
                      <div className="flex justify-between gap-2">
                        <dt className="text-muted">Mark</dt>
                        <dd className="tabular-nums">{price(p.mark)}</dd>
                      </div>
                      <div className="flex justify-between gap-2">
                        <dt className="text-muted">Relevé</dt>
                        <dd className="tabular-nums">{p.marked_at ? dateTime(p.marked_at) : "pas encore"}</dd>
                      </div>
                    </dl>
                    <div className="mt-3 flex items-end justify-between gap-3 border-t border-line/70 pt-3 text-xs">
                      <div>
                        <div>{day(p.expiration)}</div>
                        <State p={p} />
                      </div>
                      {p.can_close ? <CloseButton id={p.id} /> : null}
                    </div>
                    <div className="mt-3 border-t border-line/70 pt-3">
                      <ExecutionDetails p={p} />
                    </div>
                  </article>
                ))}
              </div>
              <div className="hidden overflow-x-auto rounded-2xl border border-line bg-surface md:block">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
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
                      <Fragment key={p.id}>
                        <tr className="border-t border-line/70">
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
                            <div className="text-xs opacity-60">
                              {p.marked_at ? dateTime(p.marked_at) : "pas encore"}
                            </div>
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
                        <tr>
                          <td colSpan={10} className="px-3 pb-3">
                            <ExecutionDetails p={p} />
                          </td>
                        </tr>
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {data.share_lots.length > 0 ? (
            <div className="space-y-2">
              <h2 className="font-display text-lg font-bold">Actions détenues (True Wheel)</h2>
              <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
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
                      <tr key={lot.id} className="border-t border-line/70">
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
