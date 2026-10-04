import Link from "next/link";

import { ApiDown, Stat } from "@/components/Stat";
import { getDashboard, getHealth } from "@/lib/api";
import { dateTime, pct, pnlClass, signed, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const [board, health] = await Promise.all([getDashboard(), getHealth()]);

  if (!board) {
    return (
      <section className="space-y-6">
        <h1 className="text-2xl font-semibold">Tableau de bord</h1>
        <ApiDown />
      </section>
    );
  }

  const limit = board.capital * board.max_engaged_pct;
  const used = limit > 0 ? Math.min(board.engaged / limit, 1) : 0;

  return (
    <section className="space-y-6">
      <h1 className="text-2xl font-semibold">Tableau de bord</h1>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Capital" value={usd(board.capital)} hint={`Départ ${usd(board.starting_capital)}`} />
        <Stat label="Disponible" value={usd(board.available)} hint="Capital moins le capital immobilisé" />
        <Stat
          label="Immobilisé"
          value={usd(board.engaged)}
          hint={`${pct(board.capital > 0 ? board.engaged / board.capital : 0)} du capital`}
        />
        <Stat
          label="Encore engageable"
          value={usd(board.engagement_capacity)}
          hint={`Limite ${pct(board.max_engaged_pct)}, ${usd(board.capital * board.max_trade_pct)} max par trade`}
        />
      </div>

      <div className="rounded-lg border border-black/10 p-4 dark:border-white/10">
        <div className="mb-2 flex justify-between text-sm">
          <span>Capital immobilisé sur la limite de {pct(board.max_engaged_pct)}</span>
          <span className="tabular-nums">
            {usd(board.engaged)} / {usd(limit)}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded bg-black/10 dark:bg-white/10">
          <div
            className={`h-full ${used >= 0.9 ? "bg-red-500" : used >= 0.7 ? "bg-amber-500" : "bg-emerald-500"}`}
            style={{ width: `${used * 100}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="P&L réalisé" value={signed(board.realized_pnl)} valueClass={pnlClass(board.realized_pnl)} />
        <Stat
          label="P&L latent"
          value={signed(board.unrealized_pnl)}
          valueClass={pnlClass(board.unrealized_pnl)}
          hint="Au dernier mark des positions ouvertes"
        />
        <Stat
          label="Positions"
          value={`${board.open_positions} ouverte${board.open_positions > 1 ? "s" : ""}`}
          hint={board.pending_positions ? `${board.pending_positions} ordre(s) en attente d'exécution` : undefined}
        />
        <Stat
          label="Deals du jour"
          value={`${board.proposed_opportunities} à traiter`}
          hint={`Screener : ${dateTime(board.last_screener_run)}`}
        />
      </div>

      <div className="flex gap-4 text-sm">
        <Link href="/opportunites" className="underline underline-offset-4">
          Voir les opportunités
        </Link>
        <Link href="/positions" className="underline underline-offset-4">
          Voir les positions
        </Link>
      </div>

      <p className="text-xs opacity-60">
        API {health?.status ?? "?"} · base {health?.database ?? "?"} · broker {health?.broker_env ?? "?"}. Courbe de
        P&amp;L, win rate et primes du mois : étape 5 (analytics).
      </p>
    </section>
  );
}
