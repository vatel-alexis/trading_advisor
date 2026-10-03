import { getHealth } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const health = await getHealth();

  return (
    <section className="space-y-6">
      <h1 className="text-2xl font-semibold">Tableau de bord</h1>
      <div className="rounded-lg border border-black/10 p-4 text-sm dark:border-white/10">
        <h2 className="mb-2 font-medium">État du système</h2>
        {health ? (
          <ul className="space-y-1">
            <li>API : {health.status}</li>
            <li>Base de données : {health.database}</li>
            <li>Environnement broker : {health.broker_env}</li>
          </ul>
        ) : (
          <p className="text-red-600">API injoignable.</p>
        )}
      </div>
      <p className="text-sm opacity-70">
        Les métriques (capital engagé, P&amp;L, drawdown, delta pondéré) arrivent au Sprint 5.
      </p>
    </section>
  );
}
