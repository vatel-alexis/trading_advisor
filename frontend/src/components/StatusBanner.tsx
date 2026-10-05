import Link from "next/link";

import { getStatus } from "@/lib/api";
import { dateTime } from "@/lib/format";

// Permanent banner under the menu: active profile, PAPER, worker and monitor health, last data
// update, and whether new entries are allowed (with the exact reasons when they are not).
export async function StatusBanner() {
  const status = await getStatus();
  if (!status) {
    return (
      <div className="border-b border-danger/40 bg-danger/10 px-4 py-1.5 text-center text-xs text-danger">
        État du système inconnu (API injoignable) : trading suspendu.
      </div>
    );
  }
  const allowed = status.trading_allowed;
  const check = (key: string) => status.gate.checks.find((c) => c.key === key);
  const chip = (ok: boolean | undefined) => (ok ? "text-success" : "text-danger");
  return (
    <details
      className={`group border-b text-xs ${allowed ? "border-success/30 bg-success/5" : "border-danger/40 bg-danger/10"}`}
    >
      <summary className="mx-auto flex max-w-6xl cursor-pointer list-none flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 [&::-webkit-details-marker]:hidden">
        <span className={`font-semibold ${allowed ? "text-success" : "text-danger"}`}>
          {allowed ? "● Trading autorisé" : "● Trading suspendu"}
        </span>
        <span className="rounded-full border border-warning/60 px-2 font-mono text-[10px] tracking-[0.12em] text-warning uppercase">
          {status.environment}
        </span>
        <Link href="/reglages" className="text-muted hover:text-foreground">
          Profil <b className="text-foreground">{status.profile ?? "—"}</b> v{status.profile_version}
        </Link>
        <span className={chip(check("worker")?.ok)}>Worker {dateTime(status.worker_last_success)}</span>
        <span className={chip(check("monitor")?.ok)}>Moniteur {dateTime(status.monitor_last_success)}</span>
        <span className={chip(check("data")?.ok)}>Données {dateTime(status.data_last_update)}</span>
        <span className="ml-auto text-muted group-open:hidden">Détails ▾</span>
      </summary>
      <ul className="mx-auto max-w-6xl space-y-0.5 px-4 pb-2">
        {status.gate.checks.map((c) => (
          <li key={c.key} className={c.ok ? "text-muted" : "text-danger"}>
            {c.ok ? "✓" : "✗"} {c.detail}
          </li>
        ))}
      </ul>
    </details>
  );
}
