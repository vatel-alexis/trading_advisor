import Link from "next/link";

import { getPeaAlert } from "@/lib/api";
import { day } from "@/lib/format";

// Reminder under the status banner for a few days after a month-end signal that changed the
// allocation of at least one PEA level.
export async function PeaAlert() {
  const alert = await getPeaAlert();
  if (!alert?.active) return null;
  return (
    <Link
      href="/pea"
      className="block border-b border-warning/40 bg-warning/10 px-4 py-1.5 text-center text-xs text-warning hover:underline"
    >
      PEA : nouvelle répartition au {day(alert.signal_day)} pour {alert.levels.join(", ")}. Voir les ajustements ›
    </Link>
  );
}
