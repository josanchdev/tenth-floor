import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

interface RunButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  running?: boolean;
}

/**
 * Primary CTA — Run button. Purple gradient + breathing glow while idle.
 * Disabled and label-swapped while a run is active (running=true).
 *
 * This is one of the two sanctioned idle motions in the whole app
 * (the other being the analyzing asset card pulse). See DESIGN.md.
 */
export function RunButton({
  running = false,
  className,
  disabled,
  children,
  ...props
}: RunButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      className={cn(
        "group relative inline-flex items-center gap-3 rounded-card px-8 py-[18px]",
        "bg-gradient-to-br from-purple to-purple-deep",
        "font-sans text-[13px] font-medium uppercase tracking-[0.08em] text-text",
        "transition-transform duration-200 ease-[var(--ease-out-smooth)]",
        "hover:-translate-y-[1px]",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0",
        running && "opacity-80",
        !disabled && !running && "[animation:run-button-breathe_3s_ease-in-out_infinite]",
        className,
      )}
      {...props}
    >
      <span>{running ? "Running…" : (children ?? "Run pipeline")}</span>
      {running && (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-text [animation:status-dot-pulse_1.4s_ease-in-out_infinite]" />
      )}
    </button>
  );
}
