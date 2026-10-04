"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Tableau de bord" },
  { href: "/opportunites", label: "Opportunités" },
  { href: "/positions", label: "Positions" },
  { href: "/historique", label: "Historique" },
  { href: "/reglages", label: "Réglages" },
  { href: "/backtests", label: "Backtests" },
];

// Main menu: one row on desktop, a second row that scrolls sideways on phones (as on the CV).
export function NavLinks() {
  const pathname = usePathname();
  return (
    <ul className="order-3 -mx-4 flex basis-full gap-1 overflow-x-auto px-4 pb-2 text-sm whitespace-nowrap [scrollbar-width:none] md:order-none md:mx-0 md:basis-auto md:px-0 md:pb-0 [&::-webkit-scrollbar]:hidden">
      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`block rounded-full px-3 py-1.5 transition-colors ${
                active
                  ? "bg-accent/10 font-medium text-accent ring-1 ring-accent/40"
                  : "text-muted hover:text-foreground"
              }`}
            >
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
