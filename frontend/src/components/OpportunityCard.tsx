"use client";

import { useState, useTransition } from "react";

import { acceptOpportunity, rejectOpportunity } from "@/app/actions";
import type { ActionResult, Opportunity } from "@/lib/api";
import { STRATEGY_LABEL, day, newKey, pct, price, usd } from "@/lib/format";

const REASONS = [
  { value: "premium_too_low", label: "Prime trop faible" },
  { value: "sector_concentration", label: "Concentration sectorielle" },
  { value: "no_conviction", label: "Pas de conviction" },
  { value: "news", label: "Actualité" },
  { value: "other", label: "Autre" },
];

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="opacity-60">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}

export function OpportunityCard({ deal }: { deal: Opportunity }) {
  const [mode, setMode] = useState<"idle" | "accept" | "reject">("idle");
  const [limit, setLimit] = useState(deal.credit.toFixed(2));
  const [reason, setReason] = useState(REASONS[0].value);
  const [note, setNote] = useState("");
  const [result, setResult] = useState<ActionResult | null>(null);
  // One key per decision, kept across retries so a resend never doubles the order.
  const [key, setKey] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const strikes = deal.legs.map(
    (leg) => `${leg.side === "sell" ? "−" : "+"}${price(leg.strike)}${leg.type === "put" ? "P" : "C"}`,
  );
  const limitValue = Number(limit.replace(",", "."));
  const limitValid = Number.isFinite(limitValue) && limitValue > 0;

  function submit(action: (k: string) => Promise<ActionResult>) {
    const k = key ?? newKey();
    setKey(k);
    startTransition(async () => {
      const r = await action(k);
      setResult(r);
      if (r.ok) setMode("idle");
    });
  }

  return (
    <article className="flex flex-col rounded-lg border border-black/10 p-4 dark:border-white/10">
      <header className="mb-3 flex items-start justify-between gap-2">
        <div>
          <div className="text-lg font-semibold">{deal.underlying}</div>
          <div className="text-xs opacity-60">
            {STRATEGY_LABEL[deal.strategy]} · {deal.sector ?? "secteur ?"} · cours {price(deal.underlying_price)}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-sm">{strikes.join(" ")}</div>
          <div className="text-xs opacity-60">
            {day(deal.expiration)} · {deal.dte} j
          </div>
        </div>
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <Row label="Delta" value={deal.delta.toFixed(2)} />
        <Row label="PoP" value={pct(deal.pop)} />
        <Row label="Rendement / risque" value={pct(deal.ror)} />
        <Row label="AROC" value={pct(deal.aroc)} />
        <Row label="Crédit" value={`${price(deal.credit)} × ${deal.quantity}`} />
        <Row label="Prime totale" value={usd(deal.credit_total)} />
        <Row label="Capital requis" value={usd(deal.collateral)} />
        <Row label="Poids" value={pct(deal.weight)} />
        <Row label="Objectif de gain" value={`${usd(deal.take_profit_gain)}`} />
        <Row label="Perte max" value={usd(deal.max_loss)} />
        <Row label="Point mort" value={price(deal.breakeven)} />
        <Row label="IV Rank" value={deal.iv_rank == null ? "—" : deal.iv_rank.toFixed(0)} />
      </dl>
      <p className="mt-2 text-xs opacity-60">
        {deal.take_profit_price != null ? `Rachat auto à ${price(deal.take_profit_price)}` : "Pas d'objectif de gain"}
        {deal.stop_price != null ? `, stop à ${price(deal.stop_price)}` : ", pas de stop"}
        {deal.time_exit_date ? `, sortie le ${day(deal.time_exit_date)}` : ""}.
        {deal.next_earnings ? ` Résultats le ${day(deal.next_earnings)}.` : ""}
      </p>

      <div className="mt-auto pt-4">
        {result?.ok ? (
          <p className="text-sm text-emerald-600 dark:text-emerald-400">{result.message}</p>
        ) : mode === "idle" ? (
          <div className="flex gap-2">
            <button
              onClick={() => setMode("accept")}
              className="flex-1 rounded bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700"
            >
              Accepter
            </button>
            <button
              onClick={() => setMode("reject")}
              className="flex-1 rounded bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700"
            >
              Rejeter
            </button>
          </div>
        ) : mode === "accept" ? (
          <div className="space-y-2 text-sm">
            <label className="flex items-center justify-between gap-2">
              <span>Crédit limite par action</span>
              <input
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                inputMode="decimal"
                className="w-24 rounded border border-black/20 bg-transparent px-2 py-1 text-right tabular-nums dark:border-white/20"
              />
            </label>
            <p className="text-xs opacity-60">
              Ordre limite du jour sur le compte paper : {deal.quantity} contrat(s) à{" "}
              {limitValid ? price(limitValue) : "—"} de crédit, soit{" "}
              {limitValid ? usd(limitValue * 100 * deal.quantity) : "—"}.
            </p>
            <div className="flex gap-2">
              <button
                disabled={pending || !limitValid}
                onClick={() =>
                  submit((k) => acceptOpportunity(deal.id, k, limitValue === deal.credit ? null : limitValue))
                }
                className="flex-1 rounded bg-emerald-600 px-3 py-2 font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                {pending ? "Envoi…" : "Confirmer l'ordre"}
              </button>
              <button
                disabled={pending}
                onClick={() => setMode("idle")}
                className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
              >
                Annuler
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-2 text-sm">
            <select
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/20"
            >
              {REASONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              placeholder="Note (facultatif)"
              className="w-full rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/20"
            />
            <div className="flex gap-2">
              <button
                disabled={pending}
                onClick={() => submit((k) => rejectOpportunity(deal.id, k, reason, note))}
                className="flex-1 rounded bg-red-600 px-3 py-2 font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {pending ? "Envoi…" : "Confirmer le rejet"}
              </button>
              <button
                disabled={pending}
                onClick={() => setMode("idle")}
                className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
              >
                Annuler
              </button>
            </div>
          </div>
        )}
        {result && !result.ok ? <p className="mt-2 text-sm text-red-600">{result.message}</p> : null}
      </div>
    </article>
  );
}
