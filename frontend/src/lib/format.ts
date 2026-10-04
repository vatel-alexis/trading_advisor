import type { ParamField, ParamValue, Strategy } from "@/lib/api";

const money = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD" });
const number2 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct1 = new Intl.NumberFormat("fr-FR", { style: "percent", maximumFractionDigits: 1 });

export const usd = (value: number | null | undefined) => (value == null ? "—" : money.format(value));

// Per-share option price, e.g. 1,05.
export const price = (value: number | null | undefined) => (value == null ? "—" : number2.format(value));

export const pct = (value: number | null | undefined) => (value == null ? "—" : pct1.format(value));

export const signed = (value: number | null | undefined) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${money.format(value)}`;

export const pnlClass = (value: number | null | undefined) =>
  value == null || value === 0
    ? ""
    : value > 0
      ? "text-emerald-600 dark:text-emerald-400"
      : "text-red-600 dark:text-red-400";

// "2026-10" -> "oct. 2026".
export function month(value: string) {
  return new Date(`${value}-15T12:00:00`).toLocaleDateString("fr-FR", { month: "short", year: "numeric" });
}

export function day(value: string | null | undefined) {
  if (!value) return "—";
  // Dates without a time are calendar days: no time zone shift.
  const date = value.length === 10 ? new Date(`${value}T12:00:00`) : new Date(value);
  return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" });
}

export function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Date(value).toLocaleString("fr-FR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Paris",
  });
}

export const STRATEGY_LABEL: Record<Strategy | "shares", string> = {
  put_credit_spread: "Put credit spread",
  cash_secured_put: "Cash secured put",
  covered_call: "Covered call",
  shares: "Actions (wheel)",
};

// Exit reasons of positions and reject reasons of proposals.
export const REASON_LABEL: Record<string, string> = {
  profit_target: "Objectif de gain",
  stop_loss: "Stop",
  time_exit: "Sortie anticipée",
  manual: "Rachat manuel",
  expiration: "Expiration",
  assignment: "Assignation",
  called_away: "Actions appelées",
  premium_too_low: "Prime trop faible",
  sector_concentration: "Concentration sectorielle",
  no_conviction: "Pas de conviction",
  news: "Actualité",
  other: "Autre",
};

export const strikes = (values: (number | null)[]) =>
  values
    .filter((v): v is number => v != null)
    .map((v) => price(v))
    .join(" / ");

// Idempotency key for one click. crypto.randomUUID needs a secure context (https or
// localhost); getRandomValues also works over plain http on the local network.
export function newKey() {
  if (typeof crypto.randomUUID === "function" && window.isSecureContext) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

// A strategy parameter as shown in the settings and backtest pages.
export function paramValue(field: ParamField | undefined, value: ParamValue | undefined): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "oui" : "non";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "aucun";
  if (field?.kind === "pct") return pct(value);
  return String(value);
}

export const GROUP_LABEL: Record<string, string> = {
  etf: "ETF",
  large_cap: "Grandes valeurs",
  wheel: "Wheel",
};
