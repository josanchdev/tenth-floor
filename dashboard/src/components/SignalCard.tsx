/**
 * SignalCard — the full-detail card used for the "Published" tier on
 * Track Record. Clickable (drawer opens in Phase 8).
 *
 * Layout: symbol + status chip header → price ladder (entry / SL / TP) →
 * R:R + conviction meta → truncated rationale.
 */

import { Card, StatusChip } from "@/components/primitives";
import type { ChipVariant } from "@/components/primitives";
import type { Signal } from "@/api/types";
import { formatPrice, formatRR } from "@/lib/format";
import { cn } from "@/lib/cn";

interface SignalCardProps {
  signal: Signal;
  onClick?: () => void;
}

export function SignalCard({ signal, onClick }: SignalCardProps) {
  return (
    <Card
      className={cn(
        "group flex cursor-pointer flex-col gap-4",
        onClick && "hover:-translate-y-[1px]",
      )}
      onClick={onClick}
      role={onClick ? "button" : undefined}
    >
      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="font-mono text-[13px] font-medium tracking-[0.02em] text-text">
            {signal.pair}
          </div>
          <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
            {signal.asset_class} · {signal.timeframe}
          </div>
        </div>
        <StatusChip variant={toChipVariant(signal.status)} />
      </header>

      <PriceLadder signal={signal} />

      <div className="flex items-center justify-between border-t border-border pt-4 font-mono text-[11px] uppercase tracking-[0.12em]">
        <span className="text-text-50">
          R:R{" "}
          <span className="text-text">{formatRR(signal.reward_risk)}</span>
        </span>
        <ConvictionPill conviction={signal.conviction} />
      </div>

      {signal.strategy_rationale && (
        <p className="line-clamp-3 text-[13px] leading-[1.6] text-text-70">
          {signal.strategy_rationale}
        </p>
      )}
    </Card>
  );
}

/* ---------- sub-components ---------- */

function PriceLadder({ signal }: { signal: Signal }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <PriceRow label="Entry" value={formatPrice(signal.entry_price)} />
      <PriceRow label="Stop" value={formatPrice(signal.stop_loss)} tone="loss" />
      <PriceRow label="Target" value={formatPrice(signal.take_profit)} tone="win" />
    </div>
  );
}

function PriceRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "win" | "loss";
}) {
  return (
    <div>
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 tabular text-[14px]",
          tone === "win" && "text-purple-light",
          tone === "loss" && "text-loss",
          !tone && "text-text",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function ConvictionPill({ conviction }: { conviction: string }) {
  const toneClass =
    conviction === "high"
      ? "text-purple"
      : conviction === "medium"
      ? "text-purple-light"
      : "text-text-50";
  return (
    <span className={cn("font-mono", toneClass)}>
      {conviction} conviction
    </span>
  );
}

function toChipVariant(status: Signal["status"]): ChipVariant {
  // Track Record lists published-tier signals; we show the lifecycle
  // status (OPEN / HIT_TP / HIT_SL / EXPIRED) as the chip.
  return status;
}
