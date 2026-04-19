/**
 * LLMStatusPill — hero-level indicator for the local vLLM server.
 *
 * States:
 *   offline  → muted, "Start LLM" button
 *   starting → teal pulse, "Warming up…"
 *   ready    → purple-light, "LLM ready"
 *   failed   → loss pink, "LLM failed" + hover tooltip
 *
 * Clicking the pill toggles start/stop. The status is polled via
 * useLLMStatus (5s) and merges instant WS lifecycle events.
 */

import { cn } from "@/lib/cn";
import { useLLMStatus, useStartLLM, useStopLLM } from "@/api/queries";
import { useSubscribeToEvents } from "@/api/WebSocketProvider";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/api/queries";
import type { LLMState } from "@/api/types";

export function LLMStatusPill() {
  const { data: status, isLoading } = useLLMStatus();
  const startLLM = useStartLLM();
  const stopLLM = useStopLLM();
  const qc = useQueryClient();

  // Merge WS lifecycle events for instant transitions.
  useSubscribeToEvents((event) => {
    if (
      event.type === "llm_starting" ||
      event.type === "llm_ready" ||
      event.type === "llm_stopped" ||
      event.type === "llm_failed"
    ) {
      qc.invalidateQueries({ queryKey: queryKeys.llmStatus() });
    }
  });

  const state: LLMState = status?.state ?? "offline";
  const isPending = startLLM.isPending || stopLLM.isPending;

  const handleClick = () => {
    if (isPending) return;
    if (state === "ready") {
      stopLLM.mutate();
    } else if (state === "offline" || state === "failed") {
      startLLM.mutate();
    }
    // starting — no action, just wait.
  };

  const tone = toneForState(state);
  const label = labelForState(state, isLoading);
  const clickable = !isPending && state !== "starting";

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={!clickable}
      title={status?.last_error ?? undefined}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1.5",
        "font-mono text-[10px] uppercase tracking-[0.14em]",
        "transition-colors duration-200",
        tone.border,
        tone.text,
        clickable && "cursor-pointer hover:brightness-125",
        !clickable && "cursor-default",
      )}
    >
      <span
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          tone.dot,
        )}
      />
      <span>{label}</span>
    </button>
  );
}

interface Tone {
  border: string;
  text: string;
  dot: string;
}

function toneForState(state: LLMState): Tone {
  switch (state) {
    case "ready":
      return {
        border: "border-purple-soft",
        text: "text-purple-light",
        dot: "bg-purple-light shadow-[0_0_8px_var(--color-purple)]",
      };
    case "starting":
      return {
        border: "border-[var(--color-teal-soft)]",
        text: "text-teal",
        dot: "bg-teal shadow-[0_0_8px_var(--color-teal)] [animation:status-dot-pulse_1.4s_ease-in-out_infinite]",
      };
    case "failed":
      return {
        border: "border-[rgba(255,123,156,0.35)]",
        text: "text-loss",
        dot: "bg-loss shadow-[0_0_8px_rgba(255,123,156,0.4)]",
      };
    case "offline":
    default:
      return {
        border: "border-border",
        text: "text-text-40",
        dot: "bg-text-40",
      };
  }
}

function labelForState(state: LLMState, loading: boolean): string {
  if (loading) return "Checking…";
  switch (state) {
    case "ready": return "LLM ready";
    case "starting": return "Warming up…";
    case "failed": return "LLM failed — retry";
    case "offline": return "Start LLM";
    default: return "LLM";
  }
}
