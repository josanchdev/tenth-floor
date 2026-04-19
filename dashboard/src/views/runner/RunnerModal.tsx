/**
 * RunnerModal — full-screen overlay summoned when the user clicks
 * Run on the Track Record hero. Streams pipeline events live and
 * dismisses on completion (with a short success delay) or on error.
 *
 * Layout (top to bottom):
 *   1. Header     — title + run id + close button
 *   2. Phase rail — five stages
 *   3. Asset grid — one card per analyzed symbol
 *   4. Footer    — published / approved counters + status line
 */

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { PhaseRail } from "./PhaseRail";
import { AssetCard } from "./AssetCard";
import { useRunnerState } from "./useRunnerState";
import { queryKeys, useCancelRun } from "@/api/queries";

interface RunnerModalProps {
  runId: string | null;
  onClose: () => void;
}

const AUTO_DISMISS_MS = 2400;

export function RunnerModal({ runId, onClose }: RunnerModalProps) {
  const state = useRunnerState(runId);
  const qc = useQueryClient();
  const cancelRun = useCancelRun();

  const isTerminal =
    state.phase === "done" || state.phase === "error" || state.phase === "cancelled";
  const canCancel = !!runId && !isTerminal && !state.cancelRequested;

  const handleCancel = () => {
    if (!runId || !canCancel) return;
    state.markCancelRequested();
    cancelRun.mutate(runId);
  };

  // Escape closes the modal at any time.
  useEffect(() => {
    if (!runId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [runId, onClose]);

  // Auto-dismiss only on success — errors stick until the user closes
  // them so they have time to read the message.
  useEffect(() => {
    if (state.phase !== "done") return;
    qc.invalidateQueries({ queryKey: ["signals"] });
    qc.invalidateQueries({ queryKey: queryKeys.stats() });
    qc.invalidateQueries({ queryKey: queryKeys.activeRun() });
    const t = setTimeout(onClose, AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [state.phase, qc, onClose]);

  // On error/cancel, refresh activeRun so the hero status line snaps back to idle.
  useEffect(() => {
    if (state.phase !== "error" && state.phase !== "cancelled") return;
    qc.invalidateQueries({ queryKey: queryKeys.activeRun() });
  }, [state.phase, qc]);

  if (!runId) return null;

  const assets = Object.values(state.assets);
  const buyCount = assets.filter((a) => a.state === "buy" || a.state === "approved").length;
  const skipCount = assets.filter((a) => a.state === "skip" || a.state === "rejected").length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-bg/85 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Pipeline runner"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="mx-auto mt-16 mb-16 w-[min(1100px,calc(100%-48px))] rounded-card border border-border bg-bg/95 px-10 py-10 shadow-[0_40px_80px_rgba(0,0,0,0.6)]">
        <header className="flex items-start justify-between gap-6 border-b border-border pb-6">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-50">
              Live pipeline run
            </div>
            <h2 className="mt-2 bg-gradient-to-br from-text via-purple-light to-purple-deep bg-clip-text text-[32px] font-light leading-none tracking-[-0.02em] text-transparent">
              {phaseTitle(state.phase)}
            </h2>
            <div className="mt-3 font-mono text-[11px] tracking-[0.02em] text-text-40">
              {runId}
            </div>
          </div>
          <div className="flex items-center gap-5">
            {!isTerminal && (
              <button
                type="button"
                onClick={handleCancel}
                disabled={!canCancel}
                className="font-mono text-[10px] uppercase tracking-[0.18em] text-loss transition-colors hover:text-text disabled:cursor-not-allowed disabled:text-text-30"
                aria-label="Cancel run"
              >
                {state.cancelRequested ? "Cancelling…" : "Cancel run"}
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-40 transition-colors hover:text-text-70"
              aria-label="Close runner"
            >
              Close · esc
            </button>
          </div>
        </header>

        <div className="mt-8">
          <PhaseRail phase={state.phase} />
        </div>

        {state.phase === "error" ? (
          <ErrorPanel
            errorType={state.errorType}
            errorPhase={state.errorPhase}
            message={state.errorMessage}
          />
        ) : state.phase === "cancelled" ? (
          <CancelledPanel />
        ) : (
          <section className="mt-10">
            <div className="mb-4 flex items-center gap-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-50">
                Assets
                <span className="ml-2 text-text-40">
                  ({assets.length}/{state.totalAssets || "?"})
                </span>
              </span>
              <span className="h-px flex-1 bg-border" />
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
                {buyCount} buy · {skipCount} skip
              </span>
            </div>
            {assets.length === 0 ? (
              <EmptyGrid phase={state.phase} />
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
                {assets.map((a) => (
                  <AssetCard key={a.symbol} asset={a} />
                ))}
              </div>
            )}
          </section>
        )}

        <footer className="mt-10 flex items-center justify-between border-t border-border pt-6">
          <div className="font-mono text-[11px] tracking-[0.02em] text-text-50">
            {footerLine(state.phase, state.publishedCount, state.errorMessage)}
          </div>
          {state.startedAt && (
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40 tabular">
              {elapsed(state.startedAt, state.completedAt)}
            </div>
          )}
        </footer>
      </div>
    </div>
  );
}

function phaseTitle(phase: ReturnType<typeof useRunnerState>["phase"]): string {
  switch (phase) {
    case "starting": return "Spinning up…";
    case "macro": return "Reading the macro frame";
    case "analyze": return "Talking to TradeAnalyst";
    case "validate": return "Validating proposals";
    case "review": return "Risk review";
    case "publish": return "Publishing signals";
    case "done": return "Run complete";
    case "cancelled": return "Run cancelled";
    case "error": return "Run failed";
  }
}

function footerLine(
  phase: ReturnType<typeof useRunnerState>["phase"],
  published: number,
  error: string | null,
): string {
  if (phase === "error") return error ?? "Pipeline failed";
  if (phase === "cancelled") return "Run cancelled by operator";
  if (phase === "done") return `Published ${published} signal${published === 1 ? "" : "s"}`;
  return "Streaming live events from the pipeline.";
}

function elapsed(startedAt: number, completedAt: number | null): string {
  const end = completedAt ?? Date.now();
  const seconds = Math.max(0, Math.round((end - startedAt) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

function ErrorPanel({
  errorType,
  errorPhase,
  message,
}: {
  errorType: string | null;
  errorPhase: string | null;
  message: string | null;
}) {
  return (
    <section className="mt-10">
      <div className="rounded-card border border-[rgba(255,123,156,0.25)] bg-surface px-8 py-8">
        <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-loss">
          {errorType ?? "Pipeline error"}
          {errorPhase && <span className="text-text-40"> · phase {errorPhase}</span>}
        </div>
        <p className="mt-3 max-w-[60ch] text-[14px] leading-[1.6] text-text-70">
          {message ?? "The pipeline failed without an error message."}
        </p>
      </div>
    </section>
  );
}

function CancelledPanel() {
  return (
    <section className="mt-10">
      <div className="rounded-card border border-border bg-surface px-8 py-8">
        <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-50">
          Cancelled
        </div>
        <p className="mt-3 max-w-[60ch] text-[14px] leading-[1.6] text-text-70">
          The pipeline was cancelled by you. Any in-flight LLM call finished
          before unwinding, and no signals were published for this run.
        </p>
      </div>
    </section>
  );
}

function EmptyGrid({ phase }: { phase: ReturnType<typeof useRunnerState>["phase"] }) {
  return (
    <div className="rounded-card border border-dashed border-border bg-surface/30 px-6 py-10 text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-40">
        {phase === "starting" || phase === "macro"
          ? "Waiting for the first asset…"
          : "No assets to show yet."}
      </p>
    </div>
  );
}
