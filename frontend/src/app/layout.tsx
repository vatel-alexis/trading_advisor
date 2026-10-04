import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trading Advisor",
  description: "Paper trading d'options orienté vente de primes",
};

const NAV = [
  { href: "/", label: "Tableau de bord" },
  { href: "/opportunites", label: "Opportunités" },
  { href: "/positions", label: "Positions" },
  { href: "/historique", label: "Historique" },
  { href: "/reglages", label: "Réglages" },
  { href: "/backtests", label: "Backtests" },
];

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fr">
      <body className="antialiased">
        <header className="border-b border-black/10 dark:border-white/10">
          <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-sm">
            <span className="font-semibold">Trading Advisor</span>
            <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400">
              PAPER
            </span>
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="opacity-70 hover:opacity-100">
                {item.label}
              </Link>
            ))}
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
