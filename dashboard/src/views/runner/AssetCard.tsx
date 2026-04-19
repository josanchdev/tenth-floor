/**
 * AssetCard — one tile per asset in the runner grid. State drives the
 * visual treatment: pending dim, analyzing teal pulse, buy/approved
 * purple, skip/rejected/error muted.
 *
 * Cards transition in via the parent's grid layout — Framer Motion
 * `layoutId` magic can be added later for cross-phase movement.
 */

import { cn } from "@/lib/cn";
import type { AssetEntry } from "./useRunnerState";

export function AssetCard({ asset }: { asset: AssetEntry }) {
  const tone = stateToTone(asset.state);
  return (
    <div
      className={cn(
        "rounded-card border px-3 py-2.5 transition-all duration-300",
        tone.container,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span
          className={cn(
            "font-mono text-[12px] font-medium tracking-[0.02em]",
            tone.symbol,
          )}
        >
          {asset.symbol}
        </span>
        {asset.confidence !== undefined && (
          <span className="font-mono text-[10px] tabular text-text-40">
            {(asset.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <div
        className={cn(
          "mt-1 font-mono text-[9px] uppercase tracking-[0.18em]",
          tone.label,
        )}
      >
        {stateLabel(asset.state)}
      </div>
    </div>
  );
}

function stateLabel(state: AssetEntry["state"]): string {
  switch (state) {
    case "pending": return "queued";
    case "analyzing": return "analyzing";
    case "buy": return "buy candidate";
    case "skip": return "skipped";
    case "error": return "error";
    case "validation_failed": return "validation failed";
    case "approved": return "approved";
    case "rejected": return "rejected";
  }
}

interface Tone {
  container: string;
  symbol: string;
  label: string;
}

function stateToTone(state: AssetEntry["state"]): Tone {
  switch (state) {
    case "analyzing":
      return {
        container: "border-[var(--color-teal-soft)] bg-surface [animation:analyzing-pulse_2.4s_ease-in-out_infinite]",
        symbol: "text-text",
        label: "text-teal",
      };
    case "buy":
      return {
        container: "border-purple-soft bg-surface-raised",
        symbol: "text-text",
        label: "text-purple-light",
      };
    case "approved":
      return {
        container: "border-purple bg-surface-raised shadow-[0_0_22px_var(--color-purple-glow)]",
        symbol: "text-text",
        label: "text-purple",
      };
    case "rejected":
      return {
        container: "border-border bg-surface/40 opacity-60",
        symbol: "text-text-50",
        label: "text-text-40",
      };
    case "validation_failed":
      return {
        container: "border-[rgba(255,123,156,0.2)] bg-surface/40 opacity-70",
        symbol: "text-text-50",
        label: "text-loss",
      };
    case "error":
      return {
        container: "border-[rgba(255,123,156,0.2)] bg-surface/40",
        symbol: "text-loss",
        label: "text-loss",
      };
    case "skip":
      return {
        container: "border-border bg-surface/40 opacity-50",
        symbol: "text-text-50",
        label: "text-text-30",
      };
    case "pending":
    default:
      return {
        container: "border-border bg-surface/30",
        symbol: "text-text-50",
        label: "text-text-30",
      };
  }
}
