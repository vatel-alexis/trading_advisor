// Server-side base URL of the FastAPI backend (the browser never talks to the broker).
export const API_URL = process.env.API_URL ?? "http://localhost:8000";

// Shared secret the hosted API requires (API_TOKEN on both sides); unset on the local stack.
const AUTH: Record<string, string> = process.env.API_TOKEN ? { "X-API-Token": process.env.API_TOKEN } : {};

export type Health = { status: string; database: string; broker_env: string };

export type Strategy = "put_credit_spread" | "cash_secured_put" | "covered_call";

export type Dashboard = {
  starting_capital: number;
  realized_pnl: number;
  capital: number;
  engaged: number;
  available: number;
  max_engaged_pct: number;
  max_trade_pct: number;
  engagement_capacity: number;
  unrealized_pnl: number;
  open_positions: number;
  pending_positions: number;
  proposed_opportunities: number;
  last_screener_run: string | null;
  analytics: Analytics;
};

export type WinStats = {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  realized_pnl: number;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
};

export type CurvePoint = { date: string; pnl: number; cumulative: number; equity: number };

export type MonthRow = { month: string; premium: number; realized_pnl: number; trades: number };

// Realized basis: finished trades only; the unrealized P&L stays on the dashboard's own tile.
export type Analytics = WinStats & {
  premium_collected: number;
  premium_this_month: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  curve: CurvePoint[];
  months: MonthRow[];
  by_strategy: (WinStats & { strategy: Strategy | "shares" })[];
  exit_reasons: Record<string, number>;
};

export type OpportunityLeg = {
  symbol: string;
  type: "put" | "call";
  side: "buy" | "sell";
  strike: number;
  bid: number;
  ask: number;
  delta: number | null;
};

export type Opportunity = {
  id: number;
  underlying: string;
  sector: string | null;
  strategy: Strategy;
  status: string;
  proposed_at: string;
  expiration: string;
  dte: number;
  underlying_price: number;
  short_strike: number | null;
  legs: OpportunityLeg[];
  quantity: number;
  credit: number;
  credit_total: number;
  max_loss: number;
  collateral: number;
  weight: number | null;
  breakeven: number;
  delta: number;
  pop: number;
  ror: number | null;
  aroc: number;
  iv_rank: number | null;
  iv_rank_method: string | null;
  score: number;
  next_earnings: string | null;
  take_profit_price: number | null;
  take_profit_gain: number | null;
  stop_price: number | null;
  time_exit_date: string | null;
};

export type OptionPosition = {
  id: number;
  underlying: string;
  strategy: Strategy;
  status: "pending" | "open";
  opened_at: string | null;
  expiration: string | null;
  dte: number | null;
  contracts: number;
  legs: { symbol: string; type: string | null; side: string; strike: number | null }[];
  entry_credit: number | null;
  open_limit: number | null;
  collateral: number;
  max_loss: number | null;
  mark: number | null;
  marked_at: string | null;
  unrealized_pnl: number | null;
  profit_pct: number | null;
  take_profit_price: number | null;
  exit_order: { purpose: string; limit: number | null } | null;
  can_close: boolean;
};

export type ShareLot = {
  id: number;
  underlying: string;
  opened_at: string | null;
  shares: number;
  cost_basis: number | null;
  collateral: number;
  covered_shares: number;
};

export type Positions = { options: OptionPosition[]; share_lots: ShareLot[] };

export type HistoryKind = "closed" | "expired" | "assigned" | "canceled" | "rejected" | "ignored";

export type HistoryRow = {
  kind: HistoryKind;
  id: string;
  date: string | null;
  opened_at: string | null;
  underlying: string;
  strategy: Strategy | "shares";
  expiration: string | null;
  strikes: number[];
  quantity: number;
  credit: number | null;
  debit: number | null;
  pnl: number | null;
  reason: string | null;
  note: string | null;
};

export type History = { rows: HistoryRow[]; underlyings: string[] };

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, { headers: AUTH, cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export const getHealth = () => get<Health>("/health");
export const getDashboard = () => get<Dashboard>("/dashboard");
export const getOpportunities = () => get<Opportunity[]>("/opportunities");
export const getPositions = () => get<Positions>("/positions");
export const getHistory = (query: URLSearchParams) => get<History>(`/history?${query}`);

// --- settings lab -----------------------------------------------------------------------------

export type ParamValue = boolean | number | string[] | number[];
export type Params = Record<string, ParamValue>;

export type ParamField = {
  key: string;
  label: string;
  group: string;
  kind: "bool" | "int" | "float" | "pct" | "symbols" | "floats";
  help: string | null;
  // Switch that enables this field; the field is greyed out while it is off.
  toggle: string | null;
  min: number | null;
  max: number | null;
  step: number | null;
};

export type Profile = {
  id: number;
  name: string;
  description: string | null;
  params: Params;
  changed: string[];
  is_active: boolean;
  created_at: string | null;
  updated_at: string;
};

export type Profiles = {
  profiles: Profile[];
  active_profile_id: number | null;
  active_version: number;
  groups: { key: string; label: string }[];
  fields: ParamField[];
  defaults: Params;
};

export type BacktestSummary = {
  start: string;
  end: string;
  capital: number;
  final: number;
  cagr: number | null;
  max_drawdown: number | null;
  sharpe: number | null;
  trades: number;
  win_rate: number | null;
  avg_win: number;
  avg_loss: number;
  profit_factor: number | null;
  avg_days_held: number;
  avg_engaged_pct: number | null;
  days_with_deal_pct: number | null;
  open_at_end: number;
  benchmark_final: number;
  benchmark_cagr: number | null;
  benchmark_drawdown: number | null;
  note?: string;
};

export type BreakdownRow = { key: string; trades: number; win_rate: number | null; pnl: number; avg: number };

export type BacktestTrade = {
  underlying: string;
  strategy: Strategy;
  group: string;
  sector: string | null;
  entry_day: string;
  expiration: string;
  strikes: number[];
  quantity: number;
  credit: number;
  short_delta: number;
  pop: number;
  iv_rank: number | null;
  exit_day: string | null;
  exit_reason: string | null;
  exit_price: number | null;
  pnl: number;
  days_held: number;
};

export type EquityPoint = { date: string; equity: number; engaged: number; benchmark: number | null };

export type BacktestResult = {
  equity: EquityPoint[];
  yearly: { year: number; return: number | null }[];
  breakdowns: Record<"exit_reason" | "strategy" | "group" | "underlying" | "year" | "entry_dte", BreakdownRow[]>;
  funnel: Record<string, number>;
  model: Record<string, number>;
  trades: BacktestTrade[];
};

export type BacktestStatus = "queued" | "running" | "done" | "failed";

export type BacktestRun = {
  id: number;
  profile_id: number | null;
  profile_name: string;
  status: BacktestStatus;
  progress: number;
  step: string | null;
  error: string | null;
  start: string;
  end: string;
  capital: number;
  model: Record<string, number>;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  summary: BacktestSummary | null;
  params: Params;
};

export type BacktestDetail = BacktestRun & { result: BacktestResult | null };

export type Backtests = {
  runs: BacktestRun[];
  cache: { fetched_at: string; first_day: string; last_day: string; symbols: string[]; size: number } | null;
  model_fields: { key: string; label: string }[];
  model_defaults: Record<string, number>;
  defaults: { start: string; end: string; capital: number };
};

export const getProfiles = () => get<Profiles>("/profiles");
export const getBacktests = () => get<Backtests>("/backtests");
export const getBacktest = (id: number | string) => get<BacktestDetail>(`/backtests/${id}`);

export type ActionResult = { ok: boolean; message: string };

// POST to the backend; the API's error message (in French) is passed back to the user as is.
export async function post(path: string, body: object): Promise<ActionResult & { data?: unknown }> {
  return send("POST", path, body);
}

export async function send(
  method: "POST" | "PUT" | "DELETE",
  path: string,
  body?: object,
): Promise<ActionResult & { data?: unknown }> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method,
      headers: { "Content-Type": "application/json", ...AUTH },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    const data: unknown = await res.json().catch(() => null);
    if (!res.ok) {
      const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : null;
      return { ok: false, message: typeof detail === "string" ? detail : `Erreur ${res.status}` };
    }
    return { ok: true, message: "", data };
  } catch {
    return { ok: false, message: "API injoignable." };
  }
}
