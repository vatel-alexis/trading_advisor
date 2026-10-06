import type { OptionPosition } from "@/lib/api";
import { price } from "@/lib/format";

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-muted">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}

// Quotes against fills for one position, per share, and the stop signals that apply to it.
export function ExecutionDetails({ p }: { p: OptionPosition }) {
  const e = p.execution;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted">Exécution et stop</summary>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <dl className="space-y-0.5">
          <div className="font-medium">Entrée (crédit)</div>
          <Line label="Mid" value={price(e.entry_mid)} />
          <Line label="Naturel" value={price(e.entry_natural)} />
          <Line label="Exécuté" value={price(e.entry_fill)} />
          <Line label="Glissement observé" value={price(e.entry_slippage)} />
          <Line label="Glissement estimé" value={price(e.entry_slippage_estimated)} />
        </dl>
        <dl className="space-y-0.5">
          <div className="font-medium">Rachat maintenant (débit)</div>
          <Line label="Mid" value={price(e.mid)} />
          <Line label="Naturel" value={price(e.natural)} />
          <Line label="Écart de liquidation" value={price(e.liquidation_spread)} />
          <Line label="Prix de rachat attendu" value={price(e.expected_exit)} />
          <Line label="Delta jambe vendue" value={e.delta == null ? "donnée absente" : e.delta.toFixed(2)} />
          <Line label="Sous-jacent" value={e.underlying_price == null ? "donnée absente" : price(e.underlying_price)} />
        </dl>
      </div>
      {e.stop_rules.length > 0 ? (
        <div className="mt-3">
          <div className="font-medium">
            Stop{e.stop_price != null ? ` (seuil ${price(e.stop_price)}, sur le crédit exécuté)` : ""} : un seul signal
            suffit
          </div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {e.stop_rules.map((r) => (
              <li key={r.key}>
                <span className="font-medium">{r.label}</span> : {r.rule}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mt-3">Pas de stop : position couverte par les actions ou assignation acceptée.</p>
      )}
      <p className="mt-2 opacity-70">
        Les ordres partent du mid et rejoignent le naturel en {e.limit_steps} palier(s) (stop et sortie anticipée
        commencent un palier plus loin). Un stop envoie un ordre limite : il ne garantit pas le prix d&apos;exécution
        (gap à l&apos;ouverture, écart bid/ask qui s&apos;élargit).
      </p>
    </details>
  );
}
