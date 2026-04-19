/**
 * TanStack Query hooks for the FastAPI backend.
 *
 * Query keys are namespaced so cache invalidations are precise.
 * All hooks are read-only GETs except `useStartRun`, which mutates.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  ActiveRunResponse,
  AssetClass,
  EquityCurveResponse,
  LLMStatusResponse,
  RunEventsResponse,
  SignalStatus,
  SignalsResponse,
  StartRunBody,
  StartRunResponse,
  StatsResponse,
} from "./types";

export const queryKeys = {
  stats: (lookbackDays?: number) => ["stats", lookbackDays ?? 365] as const,
  signals: (filters: SignalsFilters) => ["signals", filters] as const,
  equityCurve: (lookbackDays?: number) => ["equity_curve", lookbackDays ?? 365] as const,
  activeRun: () => ["runs", "active"] as const,
  latestRun: () => ["runs", "latest"] as const,
  runEvents: (runId: string) => ["runs", runId] as const,
  llmStatus: () => ["llm", "status"] as const,
};

export interface SignalsFilters {
  status?: SignalStatus;
  asset_class?: AssetClass;
  tier?: "PUBLISHED" | "SESSION";
  lookback_days?: number;
  include_expired_session?: boolean;
}

export function useStats(lookbackDays = 365) {
  return useQuery({
    queryKey: queryKeys.stats(lookbackDays),
    queryFn: () => api.get<StatsResponse>("/stats", { lookback_days: lookbackDays }),
    staleTime: 30_000,
  });
}

export function useEquityCurve(lookbackDays = 365) {
  return useQuery({
    queryKey: queryKeys.equityCurve(lookbackDays),
    queryFn: () =>
      api.get<EquityCurveResponse>("/equity_curve", { lookback_days: lookbackDays }),
    staleTime: 30_000,
  });
}

export function useSignals(filters: SignalsFilters = {}) {
  return useQuery({
    queryKey: queryKeys.signals(filters),
    queryFn: () => api.get<SignalsResponse>("/signals", { ...filters }),
    staleTime: 30_000,
  });
}

export function useActiveRun() {
  return useQuery({
    queryKey: queryKeys.activeRun(),
    queryFn: () => api.get<ActiveRunResponse>("/runs/active"),
    // Poll lightly as a belt-and-braces fallback to the WebSocket.
    refetchInterval: 10_000,
  });
}

export function useLatestRun() {
  return useQuery({
    queryKey: queryKeys.latestRun(),
    queryFn: () => api.get<RunEventsResponse>("/runs/latest"),
    staleTime: 30_000,
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: StartRunBody = {}) =>
      api.post<StartRunResponse>("/runs", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.activeRun() });
    },
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api.post<{ run_id: string; cancelled: boolean }>(`/runs/${runId}/cancel`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.activeRun() });
    },
  });
}

/* ---------- LLM lifecycle ---------- */

export function useLLMStatus() {
  return useQuery({
    queryKey: queryKeys.llmStatus(),
    queryFn: () => api.get<LLMStatusResponse>("/llm/status"),
    refetchInterval: 5_000,
  });
}

export function useStartLLM() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<LLMStatusResponse>("/llm/start"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.llmStatus() });
    },
  });
}

export function useStopLLM() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<LLMStatusResponse>("/llm/stop"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.llmStatus() });
    },
  });
}
