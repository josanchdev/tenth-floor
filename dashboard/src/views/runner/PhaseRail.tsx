/**
 * PhaseRail — five-stage horizontal progress indicator across the
 * top of the runner modal. Pure presentation; takes the active phase
 * and renders state per stage.
 */

import { cn } from "@/lib/cn";
import type { RunnerPhase } from "./useRunnerState";

const STAGES: { id: RunnerPhase; label: string }[] = [
  { id: "macro", label: "Macro" },
  { id: "analyze", label: "Analyze" },
  { id: "validate", label: "Validate" },
  { id: "review", label: "Review" },
  { id: "publish", label: "Publish" },
];

const ORDER: RunnerPhase[] = ["starting", "macro", "analyze", "validate", "review", "publish", "done"];

function stageState(phase: RunnerPhase, stage: RunnerPhase): "done" | "active" | "pending" {
  if (phase === "error" || phase === "cancelled") return "pending";
  const phaseIdx = ORDER.indexOf(phase);
  const stageIdx = ORDER.indexOf(stage);
  if (phase === "done") return "done";
  if (stageIdx < phaseIdx) return "done";
  if (stageIdx === phaseIdx) return "active";
  return "pending";
}

export function PhaseRail({ phase }: { phase: RunnerPhase }) {
  return (
    <div className="flex items-center gap-3">
      {STAGES.map((stage, i) => {
        const s = stageState(phase, stage.id);
        return (
          <div key={stage.id} className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-block h-1.5 w-1.5 rounded-full transition-all duration-300",
                  s === "done" && "bg-purple-light shadow-[0_0_10px_var(--color-purple-glow)]",
                  s === "active" &&
                    "bg-teal shadow-[0_0_12px_var(--color-teal-soft)] [animation:status-dot-pulse_2s_ease-in-out_infinite]",
                  s === "pending" && "bg-text-30",
                )}
              />
              <span
                className={cn(
                  "font-mono text-[10px] uppercase tracking-[0.18em] transition-colors duration-300",
                  s === "done" && "text-text-70",
                  s === "active" && "text-text",
                  s === "pending" && "text-text-30",
                )}
              >
                {stage.label}
              </span>
            </div>
            {i < STAGES.length - 1 && (
              <span
                className={cn(
                  "h-px w-8 transition-colors duration-300",
                  s === "done" ? "bg-purple-soft" : "bg-border",
                )}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
