import { ApiDown } from "@/components/Stat";
import { OpportunityCard } from "@/components/OpportunityCard";
import { getOpportunities } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const deals = await getOpportunities();

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Opportunités du jour</h1>
        <p className="text-sm opacity-70">
          Le screener tourne chaque jour de bourse à 10 h 30 (New York). Les deals non traités expirent au passage
          suivant.
        </p>
      </div>
      {deals === null ? (
        <ApiDown />
      ) : deals.length === 0 ? (
        <p className="rounded-lg border border-black/10 p-4 text-sm opacity-70 dark:border-white/10">
          Aucun deal en attente. Soit le screener n&apos;a rien trouvé, soit la limite d&apos;engagement est atteinte.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {deals.map((deal) => (
            <OpportunityCard key={deal.id} deal={deal} />
          ))}
        </div>
      )}
    </section>
  );
}
