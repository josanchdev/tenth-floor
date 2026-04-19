import { Card } from "./Card";
import { cn } from "@/lib/cn";

interface KPIProps {
  label: string;
  /** Numeric display — rendered with tabular figures. Accepts string for "—" or formatted values. */
  value: string;
  /** Optional secondary line under the value. */
  delta?: string;
  /** Color variant for the delta. */
  deltaTone?: "positive" | "negative" | "neutral";
  /** Apply the brand gradient to the primary value. Reserve for the single highlight KPI per view. */
  accent?: boolean;
}

/**
 * KPI tile — label / value / delta.
 * Numeric values use tabular figures so digits don't jitter on update.
 */
export function KPI({
  label,
  value,
  delta,
  deltaTone = "positive",
  accent = false,
}: KPIProps) {
  return (
    <Card className="flex flex-col gap-3 hover:border-border hover:bg-surface">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
        {label}
      </div>
      <div
        className={cn(
          "tabular text-[32px] font-normal leading-none tracking-[-0.02em]",
          accent
            ? "bg-gradient-to-br from-text via-purple-light to-purple-deep bg-clip-text text-transparent"
            : "text-text",
        )}
      >
        {value}
      </div>
      {delta && (
        <div
          className={cn(
            "font-mono text-[12px]",
            deltaTone === "positive" && "text-purple-light",
            deltaTone === "negative" && "text-loss",
            deltaTone === "neutral" && "text-text-50",
          )}
        >
          {delta}
        </div>
      )}
    </Card>
  );
}
