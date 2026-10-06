"use client";

import { useState, useTransition } from "react";

import { acceptOpportunity, rejectOpportunity } from "@/app/actions";
import type { ActionResult, Impact, Opportunity, Quality } from "@/lib/api";
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
      <dt className="text-muted">{label}</dt>
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
    <article className="flex flex-col rounded-2xl border border-line bg-surface p-4">
      <header className="mb-3 flex items-start justify-between gap-2">
        <div>
          <div className="font-display text-lg font-bold">{deal.underlying}</div>
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

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[13px] sm:text-sm">
        <Row label="|Delta| (indicatif)" value={Math.abs(deal.delta).toFixed(2)} />
        <Row label="PoP estimée" value={pct(deal.pop)} />
        <Row
          label="Distance au strike"
          value={
            deal.distance_pct == null
              ? "donnée absente"
              : `${pct(deal.distance_pct)}${deal.distance_sd == null ? "" : ` · ${deal.distance_sd.toFixed(1)} σ`}`
          }
        />
        <Row label="Détention prévue" value={deal.holding_window == null ? "donnée absente" : `${deal.holding_window} j`} />
        <Row label="Gain max" value={usd(deal.max_gain)} />
        <Row label="Rendement / risque" value={deal.return_on_risk == null ? pct(deal.ror) : pct(deal.return_on_risk)} />
        <Row label="AROC (informatif)" value={pct(deal.aroc)} />
        <Row label="Crédit" value={`${price(deal.credit)} × ${deal.quantity}`} />
        <Row label="Prime totale" value={usd(deal.credit_total)} />
        <Row label="Collatéral" value={usd(deal.collateral)} />
        <Row label="Risque / capital" value={pct(deal.risk_pct)} />
        <Row label="Objectif de gain" value={`${usd(deal.take_profit_gain)}`} />
        <Row label="Perte max" value={usd(deal.max_loss)} />
        <Row label="Stress loss" value={usd(deal.stress_loss)} />
        <Row label="Cluster" value={deal.cluster ?? "—"} />
        <Row label="Point mort" value={price(deal.breakeven)} />
        <Row label="IV Rank" value={deal.iv_rank == null ? "—" : deal.iv_rank.toFixed(0)} />
      </dl>
      {deal.quality ? <QualityDetails quality={deal.quality} /> : null}
      {deal.impact ? <ImpactDetails impact={deal.impact} /> : null}
      {deal.sizing ? <SizingDetails sizing={deal.sizing} /> : null}
      <p className="mt-2 text-xs opacity-60">
        {deal.take_profit_price != null ? `Rachat auto à ${price(deal.take_profit_price)}` : "Pas d'objectif de gain"}
        {deal.stop_price != null ? `, stop à ${price(deal.stop_price)}` : ", pas de stop"}
        {deal.time_exit_date ? `, sortie le ${day(deal.time_exit_date)}` : ""}.
        {deal.next_earnings ? ` Résultats le ${day(deal.next_earnings)}.` : ""}
        {deal.assignment_accepted ? " Assignation acceptée : les actions sont gardées puis couvertes par des calls." : ""}
        {deal.stop_price != null
          ? " Le stop envoie un ordre limite : il ne garantit pas le prix de rachat (gap, écart bid/ask)."
          : ""}
        {" "}Delta et PoP sont des estimations du modèle, pas des probabilités de gain garanties.
      </p>

      <div className="mt-auto pt-4">
        {result?.ok ? (
          <p className="text-sm text-success">{result.message}</p>
        ) : mode === "idle" ? (
          <div className="flex gap-2">
            <button
              onClick={() => setMode("accept")}
              className="flex-1 rounded-full bg-success px-4 py-2.5 text-sm font-medium text-on-success hover:brightness-110"
            >
              Accepter
            </button>
            <button
              onClick={() => setMode("reject")}
              className="flex-1 rounded-full bg-danger px-4 py-2.5 text-sm font-medium text-on-danger hover:brightness-110"
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
                className="w-24 rounded-lg border border-line-strong bg-background px-2.5 py-1.5 text-right tabular-nums"
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
                className="flex-1 rounded-full bg-success px-4 py-2.5 font-medium text-on-success hover:brightness-110 disabled:opacity-50"
              >
                {pending ? "Envoi…" : "Confirmer l'ordre"}
              </button>
              <button
                disabled={pending}
                onClick={() => setMode("idle")}
                className="rounded-full border border-line-strong px-3 hover:border-accent py-2"
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
              className="w-full rounded-lg border border-line-strong bg-background px-2.5 py-1.5"
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
              className="w-full rounded-lg border border-line-strong bg-background px-2.5 py-1.5"
            />
            <div className="flex gap-2">
              <button
                disabled={pending}
                onClick={() => submit((k) => rejectOpportunity(deal.id, k, reason, note))}
                className="flex-1 rounded-full bg-danger px-4 py-2.5 font-medium text-on-danger hover:brightness-110 disabled:opacity-50"
              >
                {pending ? "Envoi…" : "Confirmer le rejet"}
              </button>
              <button
                disabled={pending}
                onClick={() => setMode("idle")}
                className="rounded-full border border-line-strong px-3 hover:border-accent py-2"
              >
                Annuler
              </button>
            </div>
          </div>
        )}
        {result && !result.ok ? <p className="mt-2 text-sm text-danger">{result.message}</p> : null}
      </div>
    </article>
  );
}

const CAP_LABEL: Record<string, string> = {
  risk_budget: "Budget de risque du trade",
  open_risk_limit: "Perte max ouverte totale",
  cluster_limit: "Limite du cluster corrélé",
  expiration_limit: "Limite par échéance",
  capital_limit: "Collatéral disponible",
};

// "Why this number of contracts?": floor(budget / risk of one contract), then every portfolio cap.
function SizingDetails({ sizing }: { sizing: NonNullable<Opportunity["sizing"]> }) {
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-accent">Pourquoi ce nombre de contrats ?</summary>
      <p className="mt-1 opacity-80">
        Budget de risque {usd(sizing.risk_budget)} / risque d&apos;un contrat {usd(sizing.risk_per_contract)} = arrondi
        inférieur {sizing.caps.risk_budget ?? "—"}. Chaque limite du portefeuille, positions ouvertes comprises :
      </p>
      <ul className="mt-1 space-y-0.5">
        {Object.entries(sizing.caps).map(([key, value]) => (
          <li key={key} className={key === sizing.binding ? "font-semibold" : "opacity-70"}>
            {CAP_LABEL[key] ?? key} : {value >= 1_000_000 ? "sans limite" : `${value} contrat(s)`}
            {key === sizing.binding ? " ← limite retenue" : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

export const COMPONENT_LABEL: Record<string, string> = {
  execution: "Exécution et liquidité",
  safety: "Marge de sécurité",
  return: "Rendement ajusté du risque",
  time: "Fenêtre temporelle",
  regime: "Régime de marché",
  portfolio: "Adéquation au portefeuille",
  data: "Qualité des données",
};

function score(value: number | null | undefined) {
  return value == null ? "—" : `${Math.round(value * 100)}/100`;
}

export function QualityBadges({ quality }: { quality: Quality }) {
  return (
    <div className="flex flex-wrap gap-1.5 text-xs">
      {!quality.eligible ? (
        <span className="rounded-full bg-danger px-2 py-0.5 font-semibold text-on-danger">NO TRADE</span>
      ) : null}
      <span className="rounded-full border border-line px-2 py-0.5">Qualité {score(quality.absolute)}</span>
      <span className="rounded-full border border-line px-2 py-0.5">Exécution {score(quality.execution)}</span>
      <span className="rounded-full border border-line px-2 py-0.5">Portefeuille {score(quality.portfolio)}</span>
      {quality.rank ? (
        <span className="rounded-full border border-line px-2 py-0.5">
          Rang {quality.rank}/{quality.rank_of}
        </span>
      ) : null}
    </div>
  );
}

// "Why this deal?": the three scores, each component, weaknesses and missing data.
function QualityDetails({ quality }: { quality: Quality }) {
  return (
    <details className="mt-3 text-xs" open={!quality.eligible}>
      <summary className="cursor-pointer text-accent">Pourquoi ce deal ?</summary>
      <div className="mt-2 space-y-2">
        <QualityBadges quality={quality} />
        <p className="opacity-80">
          Score final {score(quality.final)} : la plus basse des trois notes, pour qu&apos;un bon rendement ne cache ni
          un marché peu liquide ni un portefeuille déjà chargé.
        </p>
        <ul className="space-y-0.5">
          {Object.entries(quality.components).map(([key, value]) => (
            <li key={key} className="flex justify-between gap-2">
              <span className="opacity-70">{COMPONENT_LABEL[key] ?? key}</span>
              <span className={`tabular-nums ${value != null && value < 0.5 ? "text-warning" : ""}`}>{score(value)}</span>
            </li>
          ))}
        </ul>
        {quality.weaknesses.length ? <p>Points faibles : {quality.weaknesses.join(", ")}.</p> : null}
        {quality.missing.length ? <p className="opacity-80">Donnée absente : {quality.missing.join(", ")}.</p> : null}
        {quality.blocking.length ? (
          <ul className="text-danger">
            {quality.blocking.map((b) => (
              <li key={b}>NO TRADE : {b}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </details>
  );
}

// The portfolio once the deal is added, against each limit.
function ImpactDetails({ impact }: { impact: Impact }) {
  const rows = [
    ["Perte max ouverte", impact.open_max_loss_after, impact.open_max_loss_limit],
    [`Cluster ${impact.cluster}`, impact.cluster_risk_after, impact.cluster_limit],
    ["Même échéance", impact.expiration_risk_after, impact.expiration_limit],
  ] as const;
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-accent">Impact sur le portefeuille</summary>
      <ul className="mt-1 space-y-0.5">
        {rows.map(([label, after, limit]) => (
          <li key={label} className={`flex justify-between gap-2 ${after > limit ? "text-danger" : ""}`}>
            <span className="opacity-70">{label}</span>
            <span className="tabular-nums">
              {usd(after)} / {usd(limit)}
            </span>
          </li>
        ))}
        <li className="flex justify-between gap-2">
          <span className="opacity-70">Stress loss après ajout</span>
          <span className="tabular-nums">{usd(impact.stress_loss_after)}</span>
        </li>
      </ul>
    </details>
  );
}
