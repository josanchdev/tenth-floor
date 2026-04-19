/**
 * Today's Signals — three tiers with decreasing visual weight:
 *
 *   1. PUBLISHED — top signals from the latest run, full cards,
 *      tracked for outcomes. Wired to GET /signals?tier=PUBLISHED.
 *   2. SESSION   — runner-up TradeAnalyst BUYs that didn't make the
 *      cut. Persisted with tier=SESSION, 24h dashboard TTL enforced
 *      server-side. Wired to GET /signals?tier=SESSION.
 *   3. SKIPPED   — TradeAnalyst SKIP decisions. Not persisted;
 *      shows a "persistence pending" empty state, collapsed by default.
 *
 * See plan.md `Signal Configuration` and DESIGN.md for the tier model.
 */

import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { SectionHeader } from "@/components/primitives";
import { SignalCard } from "@/components/SignalCard";
import { SignalChip } from "@/components/SignalChip";
import { queryKeys, useLatestRun, useSignals } from "@/api/queries";
import { useSubscribeToEvents } from "@/api/WebSocketProvider";
import type { AssetClass, Signal } from "@/api/types";

export function TodaysSignals() {
  return (
    <section className="mt-24">
      <SectionHeader label="Today's signals" />
      <PublishedTier />
      <SessionTier />
      <SkippedTier />
    </section>
  );
}

/* ---------- Published tier ---------- */

function PublishedTier() {
  const query = useSignals({ tier: "PUBLISHED", lookback_days: 1 });

  return (
    <TierWrapper label="Published" count={query.data?.count ?? 0}>
      {query.isLoading ? (
        <PublishedSkeleton />
      ) : query.isError ? (
        <TierError error={(query.error as Error).message} />
      ) : !query.data || query.data.signals.length === 0 ? (
        <EmptyPublished />
      ) : (
        <PublishedGrid signals={query.data.signals} />
      )}
    </TierWrapper>
  );
}

function PublishedGrid({ signals }: { signals: Signal[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {signals.map((s) => (
        <SignalCard key={s.signal_id} signal={s} />
      ))}
    </div>
  );
}

function PublishedSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="h-[260px] animate-pulse rounded-card border border-border bg-surface"
        />
      ))}
    </div>
  );
}

function EmptyPublished() {
  return (
    <div className="rounded-card border border-border bg-surface px-8 py-12 text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-40">
        No signals today
      </p>
      <p className="mt-3 text-[15px] text-text-70">
        Silence is the default. Hit <span className="text-purple">Run pipeline</span> when you&apos;re ready.
      </p>
    </div>
  );
}

/* ---------- Session tier ---------- */

function SessionTier() {
  // Server enforces the 24h TTL; we just ask for tier=SESSION.
  const query = useSignals({ tier: "SESSION", lookback_days: 2 });

  return (
    <TierWrapper
      label="Session signals"
      subLabel="runner-up approvals · visible 24h"
      count={query.data?.count ?? 0}
    >
      {query.isLoading ? (
        <SessionChipsSkeleton />
      ) : query.isError ? (
        <TierError error={(query.error as Error).message} />
      ) : !query.data || query.data.signals.length === 0 ? (
        <EmptySession />
      ) : (
        <SessionChipGrid signals={query.data.signals} />
      )}
    </TierWrapper>
  );
}

function SessionChipGrid({ signals }: { signals: Signal[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {signals.map((s) => (
        <SignalChip
          key={s.signal_id}
          symbol={s.pair}
          assetClass={s.asset_class}
          meta={s.conviction}
          tone="session"
        />
      ))}
    </div>
  );
}

function SessionChipsSkeleton() {
  return (
    <div className="flex flex-wrap gap-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="h-9 w-32 animate-pulse rounded-chip border border-border bg-surface"
        />
      ))}
    </div>
  );
}

function EmptySession() {
  return (
    <div className="rounded-card border border-dashed border-border bg-surface/40 px-6 py-8 text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-40">
        No runner-ups today
      </p>
      <p className="mt-2 text-[13px] text-text-70">
        Approved signals beyond the daily cap will appear here for 24h.
      </p>
    </div>
  );
}

/* ---------- Skipped tier ---------- */

interface SkipEntry {
  symbol: string;
  asset_class: AssetClass;
  rationale: string;
}

function SkippedTier() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const query = useLatestRun();

  // Invalidate latest-run cache when a new asset_analyzed event arrives.
  useSubscribeToEvents((event) => {
    if (event.type === "asset_analyzed") {
      qc.invalidateQueries({ queryKey: queryKeys.latestRun() });
    }
  });

  // Also refresh on mount so the tier hydrates without waiting for a WS event.
  useEffect(() => {
    qc.invalidateQueries({ queryKey: queryKeys.latestRun() });
  }, [qc]);

  const skips = useMemo<SkipEntry[]>(() => {
    const events = query.data?.events ?? [];
    const out: SkipEntry[] = [];
    const seen = new Set<string>();
    for (const e of events) {
      if (e.type !== "asset_analyzed") continue;
      const p = e.payload as Record<string, unknown>;
      if (p.action !== "skip") continue;
      const symbol = String(p.symbol ?? "");
      if (!symbol || seen.has(symbol)) continue;
      seen.add(symbol);
      out.push({
        symbol,
        asset_class: (p.asset_class as AssetClass) ?? "crypto",
        rationale: String(p.rationale ?? ""),
      });
    }
    return out;
  }, [query.data]);

  return (
    <TierWrapper
      label="Skipped"
      subLabel="TradeAnalyst SKIP decisions · current run"
      count={skips.length}
      collapsible
      open={open}
      onToggle={() => setOpen((v) => !v)}
    >
      {open && (
        query.isLoading ? (
          <SessionChipsSkeleton />
        ) : query.isError ? (
          <TierError error={(query.error as Error).message} />
        ) : skips.length === 0 ? (
          <EmptySkipped />
        ) : (
          <SkippedChipGrid skips={skips} />
        )
      )}
    </TierWrapper>
  );
}

function SkippedChipGrid({ skips }: { skips: SkipEntry[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {skips.map((s) => (
        <SignalChip
          key={s.symbol}
          symbol={s.symbol}
          assetClass={s.asset_class}
          meta={s.rationale ? s.rationale.slice(0, 60) : undefined}
          tone="skipped"
        />
      ))}
    </div>
  );
}

function EmptySkipped() {
  return (
    <div className="rounded-card border border-dashed border-border bg-surface/40 px-6 py-8 text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-40">
        No skips yet
      </p>
      <p className="mt-2 text-[13px] text-text-70">
        Per-asset SKIP rationales from the most recent run appear here.
      </p>
    </div>
  );
}

/* ---------- shared tier shell ---------- */

function TierWrapper({
  label,
  subLabel,
  count,
  collapsible = false,
  open = true,
  onToggle,
  children,
}: {
  label: string;
  subLabel?: string;
  count?: number;
  collapsible?: boolean;
  open?: boolean;
  onToggle?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-10 first:mt-6">
      <div className="mb-4 flex items-center gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-50">
          {label}
          {count !== undefined && (
            <span className="ml-2 text-text-40">({count})</span>
          )}
        </span>
        {subLabel && (
          <span className="font-mono text-[10px] tracking-[0.02em] text-text-30">
            · {subLabel}
          </span>
        )}
        <span className="h-px flex-1 bg-border" />
        {collapsible && (
          <button
            type="button"
            onClick={onToggle}
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40 transition-colors hover:text-text-70"
          >
            {open ? "Hide" : "Show"}
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

function TierError({ error }: { error: string }) {
  return (
    <div className="rounded-card border border-border bg-surface px-6 py-6">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-loss">
        Couldn&apos;t load signals
      </div>
      <p className="mt-2 text-[13px] text-text-70">{error}</p>
    </div>
  );
}
