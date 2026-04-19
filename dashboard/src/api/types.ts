/**
 * TypeScript contracts mirroring the FastAPI backend schemas.
 * Source of truth: src/tenth_floor/api/{signals,runs,runner}.py + events.py
 *
 * Keep this file aligned with backend changes — mismatches will only
 * surface at runtime via TanStack Query error boundaries.
 */

export type SignalStatus =
  | "OPEN"
  | "HIT_TP"
  | "HIT_SL"
  | "EXPIRED";

export type AssetClass = "crypto" | "equity" | "etf" | "commodity";

export type Conviction = "high" | "medium" | "low";

export type Direction = "long" | "short";

export type SignalTier = "PUBLISHED" | "SESSION";

export interface Signal {
  signal_id: string;
  pair: string;
  timeframe: string;
  report_date: string;
  asset_class: AssetClass;
  tier: SignalTier;
  status: SignalStatus;
  direction: Direction;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  reward_risk: number;
  confidence_score: number;
  conviction: Conviction;
  strategy_rationale: string;
  created_at: string;
  // Optional outcome fields populated post-resolution
  resolved_at?: string | null;
  mae?: number | null;
  mfe?: number | null;
}

export interface SignalsResponse {
  count: number;
  signals: Signal[];
}

export interface EquityPoint {
  signal_id: string;
  pair: string;
  asset_class: AssetClass;
  outcome_date: string;
  r: number;
  cumulative_r: number;
  status: SignalStatus;
}

export interface ConcentrationPoint {
  date: string;
  n_signals: number;
  max_class_share: number;
}

export interface EquityCurveResponse {
  points: EquityPoint[];
  concentration: ConcentrationPoint[];
}

export interface StatsResponse {
  n_resolved: number;
  n_total: number;
  open_count: number;
  wins: number;
  losses: number;
  expired: number;
  win_rate: number | null;
  expectancy_r: number | null;
  expectancy_ci_low: number | null;
  expectancy_ci_high: number | null;
  avg_rr: number | null;
}

export interface StartRunBody {
  symbols?: string[] | null;
  asset_class?: AssetClass | null;
  dry_run?: boolean;
}

export interface StartRunResponse {
  run_id: string;
}

export interface ActiveRunResponse {
  run_id: string | null;
}

/**
 * Event types the pipeline emits — matches tenth_floor.events.EventType.
 * Keep this union in lockstep with the Python StrEnum.
 */
export type EventType =
  | "pipeline_started"
  | "macro_complete"
  | "asset_analyzed"
  | "validation_result"
  | "reviewer_decision"
  | "pipeline_complete"
  | "pipeline_error"
  | "outcome_check_started"
  | "outcome_resolved"
  | "outcome_check_complete"
  | "run_complete"
  | "llm_starting"
  | "llm_ready"
  | "llm_stopped"
  | "llm_failed";

/**
 * LLM lifecycle state returned by GET /llm/status.
 * `offline` — no process tracked, /v1/models unreachable.
 * `starting` — subprocess launched, /v1/models not yet answering.
 * `ready` — /v1/models returns 200.
 * `failed` — last start attempt exited before going ready.
 */
export type LLMState = "offline" | "starting" | "ready" | "failed";

export interface LLMStatusResponse {
  state: LLMState;
  pid: number | null;
  started_at: string | null;
  model: string | null;
  base_url: string;
  last_error: string | null;
}

export interface PipelineEvent {
  run_id: string;
  type: EventType;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface RunEventsResponse {
  run_id: string;
  events: PipelineEvent[];
}
