"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

import { activateProfile, createProfile, deleteProfile, launchBacktest, saveProfile } from "@/app/actions";
import type { ActionResult, ParamField, ParamValue, Params, Profile, RiskVerdict } from "@/lib/api";
import { pct } from "@/lib/format";

// Form values are kept as typed by the user (strings for numbers and lists); they are
// converted back on submit and validated by the API, which answers in French.
type Draft = Record<string, string | boolean>;

function toDraft(field: ParamField, value: ParamValue | undefined): string | boolean {
  if (field.kind === "bool") return Boolean(value);
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value !== "number") return "";
  if (field.kind === "pct") return String(Math.round(value * 10000) / 100);
  return String(value);
}

function fromDraft(field: ParamField, value: string | boolean): ParamValue {
  if (field.kind === "bool") return Boolean(value);
  const text = String(value).trim();
  if (field.kind === "symbols")
    return text
      .split(/[,;\s]+/)
      .filter(Boolean)
      .map((s) => s.toUpperCase());
  if (field.kind === "floats")
    return text
      .split(/[,;\s]+/)
      .filter(Boolean)
      .map(Number);
  const number = Number(text.replace(",", "."));
  return field.kind === "pct" ? Math.round(number * 100) / 10000 : number;
}

function draftOf(fields: ParamField[], params: Params): Draft {
  return Object.fromEntries(fields.map((f) => [f.key, toDraft(f, params[f.key])]));
}

export function paramsOf(fields: ParamField[], draft: Draft): Params {
  return Object.fromEntries(fields.map((f) => [f.key, fromDraft(f, draft[f.key])]));
}

export function ProfileEditor({
  profile,
  fields,
  groups,
  defaults,
  backtestDefaults,
}: {
  profile: Profile;
  fields: ParamField[];
  groups: { key: string; label: string }[];
  defaults: Params;
  backtestDefaults: { start: string; end: string; capital: number };
}) {
  const router = useRouter();
  const saved = useMemo(() => draftOf(fields, profile.params), [fields, profile.params]);
  const defaultDraft = useMemo(() => draftOf(fields, defaults), [fields, defaults]);
  const [draft, setDraft] = useState<Draft>(saved);
  const [name, setName] = useState(profile.name);
  const [description, setDescription] = useState(profile.description ?? "");
  const [newName, setNewName] = useState("");
  const [result, setResult] = useState<ActionResult | null>(null);
  const [pending, startTransition] = useTransition();
  // Blocking warning before activating a profile that lost money or broke its drawdown limit.
  const [confirming, setConfirming] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const risk = profile.risk;

  const dirtyKeys = fields.filter((f) => draft[f.key] !== saved[f.key]).map((f) => f.key);
  const dirty = dirtyKeys.length > 0 || name !== profile.name || description !== (profile.description ?? "");

  function run(action: () => Promise<ActionResult & { id?: number }>, then?: (id?: number) => void) {
    startTransition(async () => {
      const r = await action();
      setResult(r);
      if (r.ok && then) then(r.id);
    });
  }

  const params = () => paramsOf(fields, draft);

  return (
    <div className="space-y-4">
      {risk ? <RiskPanel risk={risk} /> : null}
      <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs opacity-60">Nom</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT} maxLength={80} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-xs opacity-60">Description</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className={INPUT}
            maxLength={500}
          />
        </label>
      </div>

      {groups.map((group) => (
        <fieldset key={group.key} className="rounded-2xl border border-line bg-surface p-4">
          <legend className="px-1 text-sm font-semibold">{group.label}</legend>
          <div className="grid gap-x-6 gap-y-3 md:grid-cols-2">
            {fields
              .filter((f) => f.group === group.key)
              .map((f) => (
                <FieldInput
                  key={f.key}
                  field={f}
                  value={draft[f.key]}
                  defaultValue={defaultDraft[f.key]}
                  changed={dirtyKeys.includes(f.key)}
                  disabled={f.toggle != null && draft[f.toggle] === false}
                  onChange={(v) => setDraft((d) => ({ ...d, [f.key]: v }))}
                />
              ))}
          </div>
        </fieldset>
      ))}

      <div className="sticky bottom-0 space-y-2 rounded-2xl border border-line bg-surface/95 p-3 shadow-lg backdrop-blur">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <button
            disabled={pending || !dirty}
            onClick={() =>
              run(
                () => saveProfile(profile.id, name, description, params()),
                () => router.refresh(),
              )
            }
            className={PRIMARY}
          >
            Enregistrer
          </button>
          <button
            disabled={pending}
            onClick={() =>
              run(
                () =>
                  launchBacktest({
                    profile_id: profile.id,
                    params: dirtyKeys.length ? params() : undefined,
                    name: dirtyKeys.length ? `${profile.name} (non enregistré)` : undefined,
                    start: backtestDefaults.start,
                    end: backtestDefaults.end,
                    capital: backtestDefaults.capital,
                  }),
                (id) => router.push(`/backtests/${id}`),
              )
            }
            className={SECONDARY}
            title="Backtest 2019 → aujourd'hui avec les réglages affichés, même non enregistrés"
          >
            Lancer un backtest
          </button>
          {profile.is_active ? (
            <span className="rounded-full border border-success/40 bg-success/10 px-2 py-1 text-xs font-medium text-success">
              Utilisé par le screener
            </span>
          ) : (
            <button
              disabled={pending || dirty}
              onClick={() =>
                risk?.blocking
                  ? setConfirming(true)
                  : run(
                      () => activateProfile(profile.id),
                      () => router.refresh(),
                    )
              }
              className={SECONDARY}
              title={
                dirty ? "Enregistre d'abord les modifications" : "Le screener du prochain jour utilisera ce profil"
              }
            >
              Activer pour le screener
            </button>
          )}
          <button
            disabled={pending || !dirty}
            onClick={() => {
              setDraft(saved);
              setName(profile.name);
              setDescription(profile.description ?? "");
            }}
            className={LINK}
          >
            Annuler les modifications
          </button>
          <button disabled={pending} onClick={() => setDraft(defaultDraft)} className={LINK}>
            Valeurs par défaut
          </button>
          {!profile.is_active ? (
            <button
              disabled={pending}
              onClick={() => {
                if (window.confirm(`Supprimer le profil « ${profile.name} » ?`)) {
                  run(
                    () => deleteProfile(profile.id),
                    () => router.push("/reglages"),
                  );
                }
              }}
              className={`${LINK} text-danger`}
            >
              Supprimer
            </button>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Nom du nouveau profil"
            className={INPUT}
            maxLength={80}
          />
          <button
            disabled={pending || !newName.trim()}
            onClick={() =>
              run(
                () => createProfile(newName.trim(), description, params()),
                (id) => {
                  setNewName("");
                  router.push(`/reglages?profil=${id}`);
                },
              )
            }
            className={SECONDARY}
          >
            Enregistrer comme nouveau profil
          </button>
          {dirty ? <span className="text-xs text-warning">Modifications non enregistrées</span> : null}
        </div>
        {confirming && risk ? (
          <div role="alertdialog" className="space-y-2 rounded-xl border border-danger/50 bg-danger/10 p-3 text-sm">
            <p className="font-semibold text-danger">Activation déconseillée</p>
            <ul className="list-disc pl-5 text-danger">
              {risk.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <p className="text-xs opacity-70">Source : {risk.source ?? "—"}</p>
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={understood}
                onChange={(e) => setUnderstood(e.target.checked)}
                className="mt-0.5 h-4 w-4"
              />
              <span>Je comprends que ce profil a perdu de l&apos;argent ou dépassé la limite de drawdown en backtest, et je l&apos;active quand même pour le screener.</span>
            </label>
            <div className="flex gap-2">
              <button
                disabled={pending || !understood}
                onClick={() =>
                  run(
                    () => activateProfile(profile.id, true),
                    () => {
                      setConfirming(false);
                      router.refresh();
                    },
                  )
                }
                className="rounded-full bg-danger px-4 py-1.5 font-medium text-on-danger disabled:opacity-40"
              >
                Activer quand même
              </button>
              <button disabled={pending} onClick={() => setConfirming(false)} className={SECONDARY}>
                Annuler
              </button>
            </div>
          </div>
        ) : null}
        {result ? (
          <p className={`text-sm ${result.ok ? "text-success" : "text-danger"}`}>
            {pending ? "…" : result.message}
          </p>
        ) : null}
        {profile.is_active ? (
          <p className="text-xs opacity-60">
            Enregistrer ce profil change les réglages du screener dès son prochain passage. Les positions ouvertes
            gardent les règles de sortie de la version avec laquelle elles ont été ouvertes.
          </p>
        ) : null}
      </div>
    </div>
  );
}

const INPUT = "rounded-lg border border-line-strong bg-background px-2.5 py-1.5 disabled:opacity-40";
const PRIMARY = "rounded-full bg-grad px-4 py-1.5 font-medium text-on-accent disabled:opacity-40";
const SECONDARY =
  "rounded-full border border-line-strong px-3 hover:border-accent py-1.5 font-medium hover:bg-surface-2 disabled:opacity-40";
const LINK = "px-1 py-1.5 text-accent underline decoration-accent/40 underline-offset-4 opacity-70 hover:opacity-100 disabled:opacity-30";

function FieldInput({
  field,
  value,
  defaultValue,
  changed,
  disabled,
  onChange,
}: {
  field: ParamField;
  value: string | boolean;
  defaultValue: string | boolean;
  changed: boolean;
  disabled: boolean;
  onChange: (value: string | boolean) => void;
}) {
  const differs = value !== defaultValue;
  const hint = field.help ? <span className="text-xs opacity-60">{field.help}</span> : null;
  const marker = changed ? "border-l-2 border-warning pl-2" : "border-l-2 border-transparent pl-2";

  if (field.kind === "bool") {
    return (
      <label className={`flex flex-col gap-0.5 text-sm ${marker} ${disabled ? "opacity-40" : ""}`}>
        <span className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked)}
            className="h-4 w-4"
          />
          <span className="font-medium">{field.label}</span>
          {differs ? <span className="text-xs opacity-50">(défaut : {defaultValue ? "oui" : "non"})</span> : null}
        </span>
        {hint}
      </label>
    );
  }

  const isList = field.kind === "symbols" || field.kind === "floats";
  const unit = field.kind === "pct" ? "%" : null;
  const scale = field.kind === "pct" ? 100 : 1;
  return (
    <label
      className={`flex flex-col gap-1 text-sm ${marker} ${disabled ? "opacity-40" : ""} ${isList ? "md:col-span-2" : ""}`}
    >
      <span className="flex flex-wrap items-baseline gap-x-2">
        <span>{field.label}</span>
        {differs ? (
          <span className="text-xs opacity-50">
            (défaut : {String(defaultValue)}
            {unit ? ` ${unit}` : ""})
          </span>
        ) : null}
      </span>
      <span className="flex items-center gap-1">
        <input
          type={isList ? "text" : "number"}
          value={String(value)}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          min={field.min != null ? field.min * scale : undefined}
          max={field.max != null ? field.max * scale : undefined}
          step={field.kind === "int" ? 1 : field.step != null ? field.step * scale : "any"}
          className={`${INPUT} ${isList ? "w-full font-mono text-xs" : "w-32 tabular-nums"}`}
        />
        {unit ? <span className="text-xs opacity-60">{unit}</span> : null}
      </span>
      {hint}
    </label>
  );
}

// Backtest record of the profile's exact settings, shown above the form.
function RiskPanel({ risk }: { risk: RiskVerdict }) {
  const tone =
    risk.status === "ok"
      ? "border-success/40 bg-success/10 text-success"
      : risk.status === "untested"
        ? "border-line bg-surface text-muted"
        : "border-danger/50 bg-danger/10 text-danger";
  const title =
    risk.status === "ok"
      ? "Backtest dans les limites"
      : risk.status === "untested"
        ? "Non testé avec ces réglages exacts"
        : risk.status === "deficit"
          ? "Profil historiquement déficitaire"
          : "Drawdown au-delà de la limite";
  return (
    <div className={`rounded-2xl border p-3 text-sm ${tone}`}>
      <div className="font-semibold">{title}</div>
      {risk.cagr != null || risk.max_drawdown != null ? (
        <div className="text-xs">
          {pct(risk.cagr)}/an · drawdown {pct(risk.max_drawdown)} · {risk.source}
        </div>
      ) : null}
      {risk.status !== "ok"
        ? risk.reasons.map((r) => (
            <div key={r} className="text-xs">
              {r}
            </div>
          ))
        : null}
    </div>
  );
}
