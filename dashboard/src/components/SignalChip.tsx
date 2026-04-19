/**
 * SignalChip — compact representation used by the Session tier and
 * (in a dimmer variant) the Skipped tier on Track Record.
 *
 * Session chips carry symbol + asset class + an "expires in Xh"
 * hint. Skipped chips are visually quiet — data, not decoration.
 */

import { cn } from "@/lib/cn";

interface SignalChipProps {
  symbol: string;
  assetClass: string;
  meta?: string;
  tone?: "session" | "skipped";
  onClick?: () => void;
}

export function SignalChip({
  symbol,
  assetClass,
  meta,
  tone = "session",
  onClick,
}: SignalChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group flex items-center gap-3 rounded-chip border px-3 py-2 transition-colors duration-150",
        tone === "session" &&
          "border-[rgba(212,179,255,0.2)] bg-surface hover:border-purple-soft hover:bg-surface-raised",
        tone === "skipped" &&
          "border-border bg-surface/40 opacity-60 hover:opacity-100",
      )}
    >
      <span
        className={cn(
          "font-mono text-[12px] font-medium tracking-[0.02em]",
          tone === "session" ? "text-text" : "text-text-50",
        )}
      >
        {symbol}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
        {assetClass}
      </span>
      {meta && (
        <span className="font-mono text-[10px] tracking-[0.02em] text-text-40">
          {meta}
        </span>
      )}
    </button>
  );
}
