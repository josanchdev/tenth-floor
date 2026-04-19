/**
 * EquityCurve — cumulative R over time, plotted from /equity_curve.
 *
 * Raw SVG, no chart library. The plan calls for Visx but this is one
 * line chart and raw SVG gives equal control with no new dependencies.
 *
 * Layout:
 *   - X axis: outcome dates (compressed evenly, not time-scaled — we
 *     have too few points to bother with proper time scaling yet)
 *   - Y axis: cumulative R
 *   - Single purple stroke + faint purple area fill
 *   - Loss-pink dots on HIT_SL points, purple-light on HIT_TP, muted on EXPIRED
 *   - Empty state: explains what's needed
 */

import { useMemo, useState } from "react";
import { SectionHeader } from "@/components/primitives";
import { useEquityCurve } from "@/api/queries";
import type { EquityPoint } from "@/api/types";
import { formatR } from "@/lib/format";

const W = 1100;
const H = 320;
const PAD_LEFT = 56;
const PAD_RIGHT = 24;
const PAD_TOP = 24;
const PAD_BOTTOM = 36;

export function EquityCurve() {
  const query = useEquityCurve(365);

  return (
    <section className="mt-24">
      <SectionHeader label="Equity curve" />
      <div className="overflow-hidden rounded-card border border-border bg-surface">
        {query.isLoading ? (
          <Skeleton />
        ) : query.isError ? (
          <ErrorBox message={(query.error as Error).message} />
        ) : !query.data || query.data.points.length === 0 ? (
          <EmptyCurve />
        ) : (
          <Chart points={query.data.points} />
        )}
      </div>
    </section>
  );
}

/* ---------- chart ---------- */

function Chart({ points }: { points: EquityPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const { path, area, dots, yMin, yMax, xFor, yFor, ticksY } = useMemo(() => {
    const cum = points.map((p) => p.cumulative_r);
    const rawMin = Math.min(0, ...cum);
    const rawMax = Math.max(0, ...cum);
    // 10% headroom on both sides so the line never kisses the frame.
    const span = rawMax - rawMin || 1;
    const yMin = rawMin - span * 0.1;
    const yMax = rawMax + span * 0.1;

    const innerW = W - PAD_LEFT - PAD_RIGHT;
    const innerH = H - PAD_TOP - PAD_BOTTOM;

    const xFor = (i: number): number => {
      if (points.length === 1) return PAD_LEFT + innerW / 2;
      return PAD_LEFT + (i / (points.length - 1)) * innerW;
    };
    const yFor = (v: number): number => {
      const t = (v - yMin) / (yMax - yMin);
      return PAD_TOP + (1 - t) * innerH;
    };

    let path = "";
    points.forEach((p, i) => {
      const x = xFor(i);
      const y = yFor(p.cumulative_r);
      path += `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)} `;
    });

    // Area fill closes the path back to the y=0 baseline.
    const baselineY = yFor(0);
    let area = "";
    if (points.length > 0) {
      const firstX = xFor(0);
      const lastX = xFor(points.length - 1);
      area =
        `M${firstX.toFixed(1)},${baselineY.toFixed(1)} ` +
        path.replace(/^M/, "L") +
        `L${lastX.toFixed(1)},${baselineY.toFixed(1)} Z`;
    }

    const dots = points.map((p, i) => ({
      x: xFor(i),
      y: yFor(p.cumulative_r),
      point: p,
      i,
    }));

    // 5 evenly-spaced ticks on the y-axis.
    const ticksY: { y: number; value: number }[] = [];
    for (let k = 0; k <= 4; k++) {
      const value = yMin + ((yMax - yMin) * k) / 4;
      ticksY.push({ y: yFor(value), value });
    }

    return { path: path.trim(), area, dots, yMin, yMax, xFor, yFor, ticksY };
  }, [points]);

  const hovered = hover !== null ? points[hover] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full"
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="equity-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-purple)" stopOpacity="0.18" />
            <stop offset="100%" stopColor="var(--color-purple)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Y grid lines + labels */}
        {ticksY.map(({ y, value }, idx) => (
          <g key={idx}>
            <line
              x1={PAD_LEFT}
              x2={W - PAD_RIGHT}
              y1={y}
              y2={y}
              stroke="var(--color-border)"
              strokeWidth="1"
            />
            <text
              x={PAD_LEFT - 8}
              y={y + 3}
              textAnchor="end"
              className="fill-text-40 font-mono text-[10px] tabular"
            >
              {formatR(value)}
            </text>
          </g>
        ))}

        {/* Zero baseline (slightly stronger) */}
        {yMin < 0 && yMax > 0 && (
          <line
            x1={PAD_LEFT}
            x2={W - PAD_RIGHT}
            y1={yFor(0)}
            y2={yFor(0)}
            stroke="var(--color-text-30)"
            strokeWidth="1"
            strokeDasharray="2 4"
          />
        )}

        {/* Area + line */}
        {area && <path d={area} fill="url(#equity-area)" />}
        <path
          d={path}
          fill="none"
          stroke="var(--color-purple-light)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Outcome dots */}
        {dots.map(({ x, y, point, i }) => (
          <circle
            key={point.signal_id}
            cx={x}
            cy={y}
            r={hover === i ? 5 : 3}
            className={dotClass(point.status)}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}

        {/* X axis: first + last date label */}
        {points.length > 0 && (
          <>
            <text
              x={PAD_LEFT}
              y={H - PAD_BOTTOM + 20}
              textAnchor="start"
              className="fill-text-40 font-mono text-[10px]"
            >
              {formatDate(points[0].outcome_date)}
            </text>
            <text
              x={W - PAD_RIGHT}
              y={H - PAD_BOTTOM + 20}
              textAnchor="end"
              className="fill-text-40 font-mono text-[10px]"
            >
              {formatDate(points[points.length - 1].outcome_date)}
            </text>
          </>
        )}
      </svg>

      {hovered && (
        <div
          className="pointer-events-none absolute rounded-card border border-purple-soft bg-bg/95 px-4 py-3 font-mono text-[11px] backdrop-blur-sm"
          style={{
            left: `${(xFor(hover!) / W) * 100}%`,
            top: `${(yFor(hovered.cumulative_r) / H) * 100}%`,
            transform: "translate(-50%, -120%)",
          }}
        >
          <div className="text-text">{hovered.pair}</div>
          <div className="mt-1 text-text-50">
            {formatDate(hovered.outcome_date)} · {hovered.status.replace("_", " ")}
          </div>
          <div className="mt-1 tabular text-text-70">
            {formatR(hovered.r)} · cum {formatR(hovered.cumulative_r)}
          </div>
        </div>
      )}

      <Footer count={points.length} />
    </div>
  );
}

function dotClass(status: EquityPoint["status"]): string {
  switch (status) {
    case "HIT_TP":
      return "fill-purple-light stroke-purple-light cursor-pointer";
    case "HIT_SL":
      return "fill-loss stroke-loss cursor-pointer";
    case "EXPIRED":
      return "fill-text-30 stroke-text-30 cursor-pointer";
    default:
      return "fill-text-50 stroke-text-50 cursor-pointer";
  }
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function Footer({ count }: { count: number }) {
  return (
    <div className="flex items-center justify-between border-t border-border px-6 py-3">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
        Cumulative R · {count} resolved
      </div>
      <div className="flex items-center gap-4 font-mono text-[10px] uppercase tracking-[0.14em] text-text-40">
        <LegendDot color="bg-purple-light" label="win" />
        <LegendDot color="bg-loss" label="loss" />
        <LegendDot color="bg-text-30" label="expired" />
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${color}`} />
      {label}
    </span>
  );
}

/* ---------- states ---------- */

function Skeleton() {
  return <div className="h-[380px] animate-pulse bg-surface" />;
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="px-8 py-16 text-center">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-loss">
        Couldn&apos;t load equity curve
      </div>
      <p className="mt-2 text-[13px] text-text-70">{message}</p>
    </div>
  );
}

function EmptyCurve() {
  return (
    <div className="px-8 py-20 text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-40">
        No resolved signals yet
      </p>
      <p className="mt-3 text-[14px] text-text-70">
        Curve renders once outcomes come in. The pipeline publishes signals
        and the outcome checker resolves them as price action plays out.
      </p>
    </div>
  );
}
