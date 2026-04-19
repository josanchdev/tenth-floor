import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/**
 * Base container primitive. All cards, modals, drawers extend from this.
 * Surface + border + 12px radius + backdrop blur per DESIGN.md.
 */
export function Card({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-card border border-border bg-surface p-6 backdrop-blur-[12px] transition-colors duration-150",
        "hover:border-border-hover hover:bg-surface-raised",
        className,
      )}
      {...props}
    />
  );
}
