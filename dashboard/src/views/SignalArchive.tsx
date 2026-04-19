/**
 * Signal Archive — View 2. The logbook (Track Record is the scoreboard).
 *
 * Layout:
 *   1. Sticky filter bar — asset class, status, conviction, lookback days
 *   2. Card stack — vertical list, one signal per row, newest first
 *
 * No drawer yet (deferred). Clicking a row toggles an inline expansion
 * that shows the full strategy_rationale — temporary until the proper
 * drawer lands.
 */

import { useMemo, useState } from "react";
import { SectionHeader } from "@/components/primitives";
import { useSignals } from "@/api/queries";
import type { AssetClass, Signal, SignalStatus } from "@/api/types";
import { formatPrice, formatRR } from "@/lib/format";
import { cn } from "@/lib/cn";

const ASSET_CLASSES: { value: AssetClass | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "crypto", label: "Crypto" },
  { value: "equity", label: "Equity" },
  { value: "etf", label: "ETF" },
  { value: "commodity", label: "Commodity" },
];

const STATUSES: { value: SignalStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "OPEN", label: "Open" },
  { value: "HIT_TP", label: "Win" },
  { value: "HIT_SL", label: "Loss" },
  { value: "EXPIRED", label: "Expired" },
];

const LOOKBACKS = [
  { value: 7, label: "7d" },
  { value: 30, label: "30d" },
  { value: 90, label: "90d" },
  { value: 365, label: "1y" },
];

export function SignalArchive() {
  const [assetClass, setAssetClass] = useState<AssetClass | "all">("all");
  const [status, setStatus] = useState<SignalStatus | "all">("all");
  const [lookback, setLookback] = useState(30);

  const query = useSignals({
    lookback_days: lookback,
    include_expired_session: true,
    ...(assetClass !== "all" ? { asset_class: assetClass } : {}),
    ...(status !== "all" ? { status } : {}),
  });

  const signals = useMemo(() => {
    const rows = query.data?.signals ?? [];
    return [...rows].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  }, [query.data]);

  return (
    <main className="mx-auto max-w-[1200px] px-12 pt-20 pb-32">
      <SectionHeader label="Signal archive" />

      <FilterBar
        assetClass={assetClass}
        setAssetClass={setAssetClass}
        status={status}
        setStatus={setStatus}
        lookback={lookback}
        setLookback={setLookback}
        count={signals.length}
      />

      <div className="mt-6 space-y-2">
        {query.isLoading ? (
          <RowSkeleton count={6} />
        ) : query.isError ? (
          <ErrorRow message={(query.error as Error).message} />
        ) : signals.length === 0 ? (
          <EmptyRow />
        ) : (
          signals.map((s) => <ArchiveRow key={s.signal_id} signal={s} />)
        )}
      </div>
    </main>
  );
}

/* ---------- filter bar ---------- */

interface FilterBarProps {
  assetClass: AssetClass | "all";
  setAssetClass: (v: AssetClass | "all") => void;
  status: SignalStatus | "all";
  setStatus: (v: SignalStatus | "all") => void;
  lookback: number;
  setLookback: (v: number) => void;
  count: number;
}

function FilterBar({
  assetClass,
  setAssetClass,
  status,
  setStatus,
  lookback,
  setLookback,
  count,
}: FilterBarProps) {
  return (
    <div className="sticky top-0 z-10 -mx-12 mt-6 border-b border-border bg-bg/90 px-12 py-4 backdrop-blur-md">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <FilterGroup label="Class">
          {ASSET_CLASSES.map((c) => (
            <FilterButton
              key={c.value}
              active={assetClass === c.value}
              onClick={() => setAssetClass(c.value)}
            >
              {c.label}
            </FilterButton>
          ))}
        </FilterGroup>

        <FilterGroup label="Status">
          {STATUSES.map((s) => (
            <FilterButton
              key={s.value}
              active={status === s.value}
              onClick={() => setStatus(s.value)}
            >
              {s.label}
            </FilterButton>
          ))}
        </FilterGroup>

        <FilterGroup label="Window">
          {LOOKBACKS.map((l) => (
            <FilterButton
              key={l.value}
              active={lookback === l.value}
              onClick={() => setLookback(l.value)}
            >
              {l.label}
            </FilterButton>
          ))}
        </FilterGroup>

        <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
          {count} {count === 1 ? "signal" : "signals"}
        </span>
      </div>
    </div>
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
        {label}
      </span>
      <div className="flex items-center gap-1">{children}</div>
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.14em] transition-colors duration-150",
        active
          ? "border-purple-soft bg-surface-raised text-purple-light"
          : "border-border text-text-50 hover:border-text-30 hover:text-text-70",
      )}
    >
      {children}
    </button>
  );
}

/* ---------- row ---------- */

function ArchiveRow({ signal }: { signal: Signal }) {
  const [expanded, setExpanded] = useState(false);
  const tone = statusTone(signal.status);

  return (
    <div className="rounded-card border border-border bg-surface transition-colors duration-150 hover:border-text-30">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="grid w-full grid-cols-[auto_120px_1fr_auto_auto_auto] items-center gap-6 px-6 py-4 text-left"
      >
        <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40 tabular">
          {formatDate(signal.report_date)}
        </div>

        <div>
          <div className="font-mono text-[14px] font-medium text-text">
            {signal.pair}
          </div>
          <div className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
            {signal.asset_class}
          </div>
        </div>

        <div className="font-mono text-[11px] tabular text-text-70">
          {formatPrice(signal.entry_price)}
          <span className="mx-2 text-text-30">·</span>
          <span className="text-loss">{formatPrice(signal.stop_loss)}</span>
          <span className="mx-2 text-text-30">·</span>
          <span className="text-purple-light">
            {formatPrice(signal.take_profit)}
          </span>
        </div>

        <div className="font-mono text-[11px] tabular text-text-50">
          R:R <span className="text-text">{formatRR(signal.reward_risk)}</span>
        </div>

        <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-purple-light">
          {signal.conviction}
        </div>

        <div
          className={cn(
            "rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.14em]",
            tone,
          )}
        >
          {statusLabel(signal.status)}
        </div>
      </button>

      {expanded && signal.strategy_rationale && (
        <div className="border-t border-border px-6 py-5">
          <p className="max-w-[80ch] text-[13px] leading-[1.7] text-text-70">
            {signal.strategy_rationale}
          </p>
        </div>
      )}
    </div>
  );
}

function statusTone(status: SignalStatus): string {
  switch (status) {
    case "HIT_TP":
      return "border-purple-soft text-purple";
    case "HIT_SL":
      return "border-[rgba(255,123,156,0.35)] text-loss";
    case "OPEN":
      return "border-[var(--color-teal-soft)] text-teal";
    case "EXPIRED":
      return "border-border text-text-30";
    default:
      return "border-border text-text-50";
  }
}

function statusLabel(status: SignalStatus): string {
  switch (status) {
    case "HIT_TP": return "win";
    case "HIT_SL": return "loss";
    case "OPEN": return "open";
    case "EXPIRED": return "expired";
    default: return status;
  }
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/* ---------- placeholder rows ---------- */

function RowSkeleton({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="h-[68px] animate-pulse rounded-card border border-border bg-surface"
        />
      ))}
    </>
  );
}

function ErrorRow({ message }: { message: string }) {
  return (
    <div className="rounded-card border border-border bg-surface p-6">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-loss">
        Couldn&apos;t load signals
      </div>
      <p className="mt-2 text-[13px] text-text-70">{message}</p>
    </div>
  );
}

function EmptyRow() {
  return (
    <div className="rounded-card border border-dashed border-border bg-surface/30 px-6 py-12 text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-40">
        No signals match these filters
      </p>
    </div>
  );
}
