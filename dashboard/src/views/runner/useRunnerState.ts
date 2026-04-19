/**
 * useRunnerState — derives modal-friendly state from the WebSocket
 * event stream for a single run_id.
 *
 * The hook is purely a reducer over PipelineEvents. It owns no
 * cross-run history; the modal opens for one run, dismisses, then
 * a new run starts the cycle over.
 *
 * Phase progression is monotonic: once we move past `analyze`, the
 * earlier phases stay marked done. The current phase is the latest
 * one we have evidence for.
 */

import { useEffect, useMemo, useState } from "react";
import { useLatestEvents } from "@/api/WebSocketProvider";
import type { PipelineEvent } from "@/api/types";

export type RunnerPhase =
  | "starting"
  | "macro"
  | "analyze"
  | "validate"
  | "review"
  | "publish"
  | "done"
  | "cancelled"
  | "error";

export type AssetState =
  | "pending"
  | "analyzing"
  | "buy"
  | "skip"
  | "error"
  | "validation_failed"
  | "approved"
  | "rejected";

export interface AssetEntry {
  symbol: string;
  asset_class?: string;
  state: AssetState;
  confidence?: number;
  rationale?: string;
}

interface RunnerState {
  runId: string | null;
  phase: RunnerPhase;
  totalAssets: number;
  assets: Record<string, AssetEntry>;
  publishedCount: number;
  approvedCount: number;
  errorType: string | null;
  errorMessage: string | null;
  errorPhase: string | null;
  /** Has the user requested cancellation for this run? */
  cancelRequested: boolean;
  startedAt: number | null;
  completedAt: number | null;
}

const INITIAL: RunnerState = {
  runId: null,
  phase: "starting",
  totalAssets: 0,
  assets: {},
  publishedCount: 0,
  approvedCount: 0,
  errorType: null,
  errorMessage: null,
  errorPhase: null,
  cancelRequested: false,
  startedAt: null,
  completedAt: null,
};

type Action = { kind: "event"; event: PipelineEvent };

const PHASE_ORDER: RunnerPhase[] = [
  "starting",
  "macro",
  "analyze",
  "validate",
  "review",
  "publish",
  "done",
];

function maxPhase(a: RunnerPhase, b: RunnerPhase): RunnerPhase {
  if (a === "error" || b === "error") return "error";
  return PHASE_ORDER.indexOf(a) >= PHASE_ORDER.indexOf(b) ? a : b;
}

function reducer(state: RunnerState, action: Action): RunnerState {
  const event = action.event;
  const payload = event.payload as Record<string, unknown>;

  switch (event.type) {
    case "pipeline_started": {
      return {
        ...state,
        runId: event.run_id,
        phase: maxPhase(state.phase, "macro"),
        totalAssets: Number(payload.total_assets ?? 0),
        startedAt: state.startedAt ?? Date.parse(event.timestamp),
      };
    }

    case "macro_complete": {
      return { ...state, phase: maxPhase(state.phase, "analyze") };
    }

    case "asset_analyzed": {
      const symbol = String(payload.symbol ?? "");
      if (!symbol) return state;
      const action = String(payload.action ?? "skip");
      const nextState: AssetState =
        action === "buy" ? "buy" : action === "error" ? "error" : "skip";
      return {
        ...state,
        phase: maxPhase(state.phase, "analyze"),
        assets: {
          ...state.assets,
          [symbol]: {
            symbol,
            asset_class: payload.asset_class as string | undefined,
            state: nextState,
            rationale: payload.rationale as string | undefined,
          },
        },
      };
    }

    case "validation_result": {
      const symbol = String(payload.symbol ?? "");
      const passed = Boolean(payload.passed);
      const existing = state.assets[symbol];
      if (!existing) return { ...state, phase: maxPhase(state.phase, "validate") };
      return {
        ...state,
        phase: maxPhase(state.phase, "validate"),
        assets: {
          ...state.assets,
          [symbol]: {
            ...existing,
            state: passed ? "buy" : "validation_failed",
          },
        },
      };
    }

    case "reviewer_decision": {
      const symbol = String(payload.symbol ?? "");
      const approved = String(payload.verdict ?? "") === "approved";
      const existing = state.assets[symbol];
      if (!existing) return { ...state, phase: maxPhase(state.phase, "review") };
      return {
        ...state,
        phase: maxPhase(state.phase, "review"),
        approvedCount: state.approvedCount + (approved ? 1 : 0),
        assets: {
          ...state.assets,
          [symbol]: {
            ...existing,
            state: approved ? "approved" : "rejected",
            confidence: payload.confidence as number | undefined,
          },
        },
      };
    }

    case "pipeline_complete": {
      return {
        ...state,
        phase: maxPhase(state.phase, "publish"),
        publishedCount: Number(payload.published_count ?? 0),
      };
    }

    case "run_complete": {
      return { ...state, phase: "done", completedAt: Date.parse(event.timestamp) };
    }

    case "pipeline_cancelled": {
      return {
        ...state,
        phase: "cancelled",
        completedAt: Date.parse(event.timestamp),
      };
    }

    case "pipeline_error": {
      return {
        ...state,
        phase: "error",
        errorType: (payload.error_type as string | undefined) ?? null,
        errorMessage: String(payload.message ?? "Pipeline failed"),
        errorPhase: (payload.phase as string | undefined) ?? null,
        completedAt: Date.parse(event.timestamp),
      };
    }

    default:
      return state;
  }
}

/**
 * Track runner state for a specific run. Pass the run_id you got
 * back from `POST /runs`. The hook will reset whenever runId changes
 * and rebuild state from incoming WS events.
 *
 * Returns state + a `markCancelRequested` helper the modal calls when
 * the user clicks Cancel (so the button can flip to "cancelling…"
 * before the backend emits `pipeline_cancelled`).
 */
export function useRunnerState(runId: string | null): RunnerState & {
  markCancelRequested: () => void;
} {
  const events = useLatestEvents();
  const [cancelRequested, setCancelRequested] = useState(false);

  useEffect(() => {
    setCancelRequested(false);
  }, [runId]);

  const state = useMemo(() => {
    if (!runId) return INITIAL;
    const forRun = events.filter((e) => e.run_id === runId);
    return forRun.reduce(
      (acc, event) => reducer(acc, { kind: "event", event }),
      { ...INITIAL, runId },
    );
  }, [events, runId]);

  return useMemo(
    () => ({
      ...state,
      cancelRequested,
      markCancelRequested: () => setCancelRequested(true),
    }),
    [state, cancelRequested],
  );
}
