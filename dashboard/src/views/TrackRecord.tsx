/**
 * Track Record — the landing view.
 *
 * Sections (top to bottom):
 *   1. Hero         — date, title, status line, Run button
 *   2. Portfolio    — KPI grid wired to /stats
 *   3. Today's      — signals placeholder (Phase 5 will fill this)
 *   4. Equity curve — placeholder (Phase 7 will fill this)
 */

import { useEffect, useRef, useState } from "react";
import { KPI, RunButton, SectionHeader } from "@/components/primitives";
import { useActiveRun, useLLMStatus, useStartRun, useStats } from "@/api/queries";
import { useWebSocketStatus } from "@/api/WebSocketProvider";
import type { StatsResponse } from "@/api/types";
import { LLMStatusPill } from "@/components/LLMStatusPill";
import { TodaysSignals } from "./trackRecord/TodaysSignals";
import { EquityCurve } from "./trackRecord/EquityCurve";
import { RunnerModal } from "./runner/RunnerModal";

export function TrackRecord() {
  const [runnerRunId, setRunnerRunId] = useState<string | null>(null);
  return (
    <>
      <main className="mx-auto max-w-[1200px] px-12 pt-20 pb-32">
        <Hero
          runnerRunId={runnerRunId}
          onOpenRunner={setRunnerRunId}
        />
        <PortfolioSnapshot />
        <TodaysSignals />
        <EquityCurve />
      </main>
      <RunnerModal runId={runnerRunId} onClose={() => setRunnerRunId(null)} />
    </>
  );
}

/* ---------- Hero ---------- */

function Hero({
  runnerRunId,
  onOpenRunner,
}: {
  runnerRunId: string | null;
  onOpenRunner: (runId: string) => void;
}) {
  const activeRun = useActiveRun();
  const wsStatus = useWebSocketStatus();
  const startRun = useStartRun();
  const llmStatus = useLLMStatus();

  const activeRunId = activeRun.data?.run_id ?? null;
  const isRunning = Boolean(activeRunId);
  const llmReady = llmStatus.data?.state === "ready";

  // On a fresh page load with a run already in flight, open the modal
  // automatically so the operator doesn't miss the live stream. After
  // they close it, we don't pop it back open — they can click the
  // status line (or the Run button) to re-attach.
  const didAutoOpen = useRef(false);
  useEffect(() => {
    if (didAutoOpen.current) return;
    if (activeRunId && !runnerRunId) {
      didAutoOpen.current = true;
      onOpenRunner(activeRunId);
    }
  }, [activeRunId, runnerRunId, onOpenRunner]);
  const today = new Date();
  const dateLine = today.toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  const statusLine = isRunning
    ? `Pipeline running · ${activeRun.data?.run_id}`
    : wsStatus === "open"
    ? "Pipeline idle · connected"
    : wsStatus === "connecting"
    ? "Connecting to backend…"
    : "Backend unreachable — is the API running on 127.0.0.1:8765?";

  const statusTone: "active" | "idle" | "error" =
    wsStatus === "closed" ? "error" : isRunning ? "active" : "idle";

  return (
    <header className="border-b border-border pb-10">
      <div className="flex items-center justify-between">
        <div className="font-mono text-[11px] uppercase tracking-[0.18em] text-text-50">
          The Tenth Floor · {dateLine}
        </div>
        <LLMStatusPill />
      </div>

      <div className="mt-8 flex items-end justify-between gap-12">
        <div>
          <h1 className="bg-gradient-to-br from-text via-purple-light to-purple-deep bg-clip-text text-[56px] font-light leading-none tracking-[-0.03em] text-transparent">
            Today&apos;s research
          </h1>
          <StatusLine
            tone={statusTone}
            onClick={
              isRunning && activeRunId
                ? () => onOpenRunner(activeRunId)
                : undefined
            }
          >
            {statusLine}
            {isRunning && (
              <span className="ml-2 text-text-40">· click to reopen</span>
            )}
          </StatusLine>
        </div>
        <RunButton
          running={startRun.isPending}
          disabled={!llmReady && !isRunning}
          onClick={() => {
            if (isRunning && activeRunId) {
              onOpenRunner(activeRunId);
              return;
            }
            startRun.mutate(
              { dry_run: false },
              { onSuccess: (data) => onOpenRunner(data.run_id) },
            );
          }}
          aria-label={isRunning ? "Reopen pipeline runner" : "Run pipeline"}
        >
          {isRunning ? "Reopen runner" : undefined}
        </RunButton>
      </div>
    </header>
  );
}

function StatusLine({
  tone,
  children,
  onClick,
}: {
  tone: "active" | "idle" | "error";
  children: React.ReactNode;
  onClick?: () => void;
}) {
  const dotClass =
    tone === "active"
      ? "bg-teal shadow-[0_0_12px_var(--color-teal)]"
      : tone === "idle"
      ? "bg-purple-light shadow-[0_0_12px_var(--color-purple)]"
      : "bg-loss shadow-[0_0_10px_rgba(255,123,156,0.6)]";
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={`mt-4 flex items-center gap-3 font-mono text-[12px] tracking-[0.02em] text-text-50 ${
        onClick ? "cursor-pointer transition-colors hover:text-text-70" : ""
      }`}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full [animation:status-dot-pulse_2s_ease-in-out_infinite] ${dotClass}`}
      />
      <span>{children}</span>
    </Tag>
  );
}

/* ---------- Portfolio snapshot (KPI grid) ---------- */

function PortfolioSnapshot() {
  const query = useStats();

  return (
    <section className="mt-16">
      <SectionHeader label="Portfolio snapshot" />
      <div className="grid grid-cols-4 gap-4">
        {query.isLoading ? (
          <KPISkeletonGrid />
        ) : query.isError ? (
          <KPIErrorState error={(query.error as Error).message} />
        ) : (
          <KPIGrid stats={query.data!} />
        )}
      </div>
    </section>
  );
}

function KPIGrid({ stats }: { stats: StatsResponse }) {
  return (
    <>
      <KPI
        label="Expectancy"
        value={formatR(stats.expectancy_r)}
        delta={formatExpectancyDelta(stats)}
        deltaTone="neutral"
        accent
      />
      <KPI
        label="Win rate"
        value={formatPercent(stats.win_rate)}
        delta={`${stats.wins}W · ${stats.losses}L · ${stats.expired}E`}
        deltaTone="neutral"
      />
      <KPI
        label="Avg R:R"
        value={stats.avg_rr !== null ? stats.avg_rr.toFixed(2) : "—"}
        delta="realized"
        deltaTone="neutral"
      />
      <KPI
        label="Open positions"
        value={stats.open_count.toString()}
        delta={`${stats.n_total} total signals`}
        deltaTone="neutral"
      />
    </>
  );
}

function KPISkeletonGrid() {
  return (
    <>
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="h-[132px] animate-pulse rounded-card border border-border bg-surface"
        />
      ))}
    </>
  );
}

function KPIErrorState({ error }: { error: string }) {
  return (
    <div className="col-span-4 rounded-card border border-border bg-surface p-6">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-loss">
        Couldn&apos;t load stats
      </div>
      <p className="mt-2 text-[13px] text-text-70">{error}</p>
    </div>
  );
}

function formatR(value: number | null): string {
  if (value === null || value === undefined) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}R`;
}

function formatPercent(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

function formatExpectancyDelta(stats: StatsResponse): string {
  if (stats.n_resolved === 0) return "no resolved signals yet";
  if (
    stats.expectancy_ci_low !== null &&
    stats.expectancy_ci_high !== null
  ) {
    return `n=${stats.n_resolved} · CI [${stats.expectancy_ci_low.toFixed(2)}, ${stats.expectancy_ci_high.toFixed(2)}]`;
  }
  return `n=${stats.n_resolved}`;
}

