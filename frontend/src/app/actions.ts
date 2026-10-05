"use server";

import { revalidatePath } from "next/cache";

import { post, send, type ActionResult, type Params } from "@/lib/api";

// The page the click came from is left alone so its confirmation stays on screen; its own
// data refreshes on the next visit (or on its auto-refresh).
function refresh(except: string) {
  for (const path of ["/", "/opportunites", "/positions", "/historique"]) {
    if (path !== except) revalidatePath(path);
  }
}

// The idempotency key is generated in the browser once per click: a double submit or a retry
// after a network error sends a single order.
export async function acceptOpportunity(
  id: number,
  idempotencyKey: string,
  limitPrice: number | null,
): Promise<ActionResult> {
  const body: Record<string, unknown> = { idempotency_key: idempotencyKey };
  if (limitPrice != null) body.limit_price = limitPrice;
  const result = await post(`/opportunities/${id}/accept`, body);
  refresh("/opportunites");
  if (!result.ok) return result;
  const data = result.data as { limit_price: number | null; order_status: string | null };
  return { ok: true, message: `Ordre envoyé à ${data.limit_price ?? "—"} (${data.order_status ?? "?"}).` };
}

export async function rejectOpportunity(
  id: number,
  idempotencyKey: string,
  reason: string,
  note: string,
): Promise<ActionResult> {
  const result = await post(`/opportunities/${id}/reject`, {
    idempotency_key: idempotencyKey,
    reason,
    note: note.trim() || null,
  });
  refresh("/opportunites");
  return result.ok ? { ok: true, message: "Deal rejeté." } : result;
}

export async function closePosition(id: number, idempotencyKey: string): Promise<ActionResult> {
  const result = await post(`/positions/${id}/close`, { idempotency_key: idempotencyKey });
  refresh("/positions");
  if (!result.ok) return result;
  const data = result.data as { limit_price: number | null };
  return { ok: true, message: `Rachat envoyé à ${data.limit_price ?? "—"}.` };
}

// --- settings lab ----------------------------------------------------------------------------

function refreshLab() {
  for (const path of ["/reglages", "/backtests", "/"]) revalidatePath(path);
  // The status banner (active profile) is in the layout of every page.
  revalidatePath("/", "layout");
}

export async function saveProfile(
  id: number,
  name: string,
  description: string,
  params: Params,
): Promise<ActionResult> {
  const result = await send("PUT", `/profiles/${id}`, { name, description, params });
  refreshLab();
  return result.ok ? { ok: true, message: "Profil enregistré." } : result;
}

export async function createProfile(
  name: string,
  description: string,
  params: Params,
): Promise<ActionResult & { id?: number }> {
  const result = await send("POST", "/profiles", { name, description, params });
  refreshLab();
  if (!result.ok) return result;
  return { ok: true, message: "Profil créé.", id: (result.data as { id: number }).id };
}

export async function activateProfile(id: number, confirmRisk = false): Promise<ActionResult> {
  const result = await send("POST", `/profiles/${id}/activate`, { confirm_risk: confirmRisk });
  refreshLab();
  if (!result.ok) return result;
  const data = result.data as { version: number };
  return { ok: true, message: `Profil actif pour le screener (version ${data.version}).` };
}

export async function deleteProfile(id: number): Promise<ActionResult> {
  const result = await send("DELETE", `/profiles/${id}`);
  refreshLab();
  return result.ok ? { ok: true, message: "Profil supprimé." } : result;
}

export type BacktestLaunch = {
  profile_id: number | null;
  params?: Params;
  name?: string;
  start: string;
  end: string;
  capital: number;
  model?: Record<string, number>;
  refresh_data?: boolean;
};

export async function launchBacktest(body: BacktestLaunch): Promise<ActionResult & { id?: number }> {
  const result = await send("POST", "/backtests", body);
  refreshLab();
  if (!result.ok) return result;
  const id = (result.data as { id: number }).id;
  return { ok: true, message: `Backtest n° ${id} en file d'attente.`, id };
}

export async function deleteBacktest(id: number): Promise<ActionResult> {
  const result = await send("DELETE", `/backtests/${id}`);
  refreshLab();
  return result.ok ? { ok: true, message: "Backtest supprimé." } : result;
}
