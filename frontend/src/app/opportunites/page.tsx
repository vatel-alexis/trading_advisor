import { ApiDown } from "@/components/Stat";
import { OpportunityCard, QualityBadges } from "@/components/OpportunityCard";
import { getNoTrade, getOpportunities } from "@/lib/api";
import { STRATEGY_LABEL, dateTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [deals, noTrade] = await Promise.all([getOpportunities(), getNoTrade()]);

  return (
    <section className="space-y-4">
      <div>
        <h1 className="font-display text-2xl font-extrabold tracking-tight md:text-3xl">Opportunités du jour</h1>
        <p className="text-sm opacity-70">
          Le screener tourne chaque jour de bourse à 10 h 30 (New York). Les deals non traités expirent au passage
          suivant.
        </p>
      </div>
      {deals === null ? (
        <ApiDown />
      ) : deals.length === 0 ? (
        <p className="rounded-2xl border border-line bg-surface p-4 text-sm opacity-70">
          Aucun deal en attente. Soit le screener n&apos;a rien trouvé, soit la limite d&apos;engagement est atteinte.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {deals.map((deal) => (
            <OpportunityCard key={deal.id} deal={deal} />
          ))}
        </div>
      )}
      {noTrade && noTrade.rows.length ? (
        <div className="rounded-2xl border border-line bg-surface p-4">
          <h2 className="font-display text-lg font-bold">NO TRADE</h2>
          <p className="mb-3 text-xs opacity-70">
            Candidats classés mais sans contrat au dernier passage
            {noTrade.run_at ? ` (${dateTime(noTrade.run_at)})` : ""}, avec chaque règle bloquante.
          </p>
          <ul className="space-y-3">
            {noTrade.rows.map((row) => (
              <li key={row.underlying} className="text-sm">
                <div className="font-semibold">
                  {row.underlying}
                  {row.strategy ? <span className="ml-2 text-xs opacity-60">{STRATEGY_LABEL[row.strategy]}</span> : null}
                </div>
                <ul className="text-xs text-danger">
                  {row.reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
                {row.quality ? (
                  <div className="mt-1">
                    <QualityBadges quality={row.quality} />
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
