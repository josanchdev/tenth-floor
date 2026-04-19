import { cn } from "@/lib/cn";

/**
 * Status pill. Semantic color scheme:
 * - PUBLISHED / HIT_TP → purple (purple = good, per DESIGN.md)
 * - SESSION           → purple-light muted
 * - OPEN              → teal (the one sanctioned teal use outside the analyzing card — open IS a live state)
 * - HIT_SL            → loss pink
 * - EXPIRED           → text-30 faded
 */

export type ChipVariant =
  | "PUBLISHED"
  | "SESSION"
  | "OPEN"
  | "HIT_TP"
  | "HIT_SL"
  | "EXPIRED";

const styles: Record<ChipVariant, string> = {
  PUBLISHED: "text-purple border-purple-soft",
  SESSION: "text-purple-light border-[rgba(212,179,255,0.3)]",
  OPEN: "text-teal border-teal-soft",
  HIT_TP: "text-purple border-purple-soft",
  HIT_SL: "text-loss border-[rgba(255,123,156,0.35)]",
  EXPIRED: "text-text-30 border-border",
};

export function StatusChip({
  variant,
  children,
}: {
  variant: ChipVariant;
  children?: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-[0.14em]",
        styles[variant],
      )}
    >
      {children ?? variant.replace("_", " ")}
    </span>
  );
}
