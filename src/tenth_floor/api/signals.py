"""Signal + stats read endpoints — SQLite-backed, no mutation."""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from tenth_floor.db.signal_logger import SignalLogger

router = APIRouter(tags=["signals"])


@router.get("/signals")
def list_signals(
    status: str | None = Query(default=None, description="OPEN / HIT_TP / HIT_SL / EXPIRED"),
    asset_class: str | None = Query(default=None),
    tier: str | None = Query(default=None, description="PUBLISHED or SESSION"),
    lookback_days: int = Query(default=90, ge=1, le=365),
    include_expired_session: bool = Query(
        default=False,
        description="Include session signals older than 24h (archive view).",
    ),
) -> dict[str, Any]:
    """Return recent signals, optionally filtered by status, asset class, or tier.

    Session-tier signals have a 24h dashboard TTL — older session rows
    are excluded unless ``include_expired_session=true``.
    """
    if tier is not None and tier not in ("PUBLISHED", "SESSION"):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier!r}")

    with SignalLogger() as sl:
        rows = sl.get_recent_signals(
            lookback_days=lookback_days,
            tier=tier,
            include_expired_session=include_expired_session,
        )

    if status:
        rows = [r for r in rows if r.get("status") == status]
    if asset_class:
        rows = [r for r in rows if r.get("asset_class") == asset_class]

    return {"count": len(rows), "signals": rows}


@router.get("/signals/{signal_id}")
def get_signal(signal_id: str) -> dict[str, Any]:
    """Return one signal by id, with full reasoning."""
    with SignalLogger() as sl:
        row = sl.get_signal(signal_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    return row


@router.get("/equity_curve")
def get_equity_curve(
    lookback_days: int = Query(default=365, ge=1, le=3650),
) -> dict[str, Any]:
    """Return cumulative R outcomes over time for the equity curve chart.

    Each point is one resolved signal in chronological order. Cumulative
    R is the running sum of:
      HIT_TP  → +reward_risk
      HIT_SL  → −1.0
      EXPIRED → 0.0

    Per-day concentration is the max share of any single asset class
    among that day's *published* set — used by the chart's diversity
    band overlay.
    """
    with SignalLogger() as sl:
        rows = sl.get_recent_signals(lookback_days=lookback_days, tier="PUBLISHED")

    resolved = [
        r for r in rows
        if r.get("status") in ("HIT_TP", "HIT_SL", "EXPIRED")
        and r.get("outcome_date")
    ]
    resolved.sort(key=lambda r: str(r.get("outcome_date") or ""))

    points = []
    cum = 0.0
    for r in resolved:
        status = r["status"]
        if status == "HIT_TP":
            r_value = float(r.get("reward_risk") or 0.0)
        elif status == "HIT_SL":
            r_value = -1.0
        else:
            r_value = 0.0
        cum += r_value
        points.append({
            "signal_id": r["signal_id"],
            "pair": r["pair"],
            "asset_class": r["asset_class"],
            "outcome_date": r["outcome_date"],
            "r": r_value,
            "cumulative_r": cum,
            "status": status,
        })

    # Per-report-date concentration: max share of any single asset class
    # among that day's published set.
    by_date: dict[str, list[str]] = {}
    for r in rows:
        by_date.setdefault(r["report_date"], []).append(r["asset_class"])
    concentration = []
    for date in sorted(by_date):
        classes = by_date[date]
        if not classes:
            continue
        counts: dict[str, int] = {}
        for c in classes:
            counts[c] = counts.get(c, 0) + 1
        max_share = max(counts.values()) / len(classes)
        concentration.append({
            "date": date,
            "n_signals": len(classes),
            "max_class_share": max_share,
        })

    return {
        "points": points,
        "concentration": concentration,
    }


@router.get("/stats")
def get_stats(lookback_days: int = Query(default=365, ge=1, le=3650)) -> dict[str, Any]:
    """Return track-record stats — expectancy in R with 95% CI.

    Expectancy = mean R outcome where winners contribute +planned_R,
    losers contribute −1R, and expired signals contribute 0. The 95%
    CI uses normal approximation (mean ± 1.96 * sem) — good enough
    once N ≥ 30. For very small N the interval is wide on purpose.
    """
    with SignalLogger() as sl:
        # Stats only count PUBLISHED — session signals aren't tracked.
        rows = sl.get_recent_signals(lookback_days=lookback_days, tier="PUBLISHED")
        open_count = sl.open_signal_count()

    resolved = [r for r in rows if r.get("status") in ("HIT_TP", "HIT_SL", "EXPIRED")]
    r_outcomes: list[float] = []
    wins = 0
    losses = 0
    expired = 0
    for r in resolved:
        status = r["status"]
        if status == "HIT_TP":
            r_outcomes.append(float(r.get("reward_risk") or 0.0))
            wins += 1
        elif status == "HIT_SL":
            r_outcomes.append(-1.0)
            losses += 1
        else:  # EXPIRED
            r_outcomes.append(0.0)
            expired += 1

    n = len(r_outcomes)
    if n == 0:
        expectancy = None
        ci_low = None
        ci_high = None
        win_rate = None
        avg_rr = None
    else:
        expectancy = sum(r_outcomes) / n
        if n >= 2:
            variance = sum((x - expectancy) ** 2 for x in r_outcomes) / (n - 1)
            sem = math.sqrt(variance / n)
            ci_low = expectancy - 1.96 * sem
            ci_high = expectancy + 1.96 * sem
        else:
            ci_low = None
            ci_high = None
        win_rate = wins / n
        rr_values = [
            float(r.get("reward_risk") or 0.0)
            for r in rows
            if r.get("reward_risk")
        ]
        avg_rr = sum(rr_values) / len(rr_values) if rr_values else None

    return {
        "n_resolved": n,
        "n_total": len(rows),
        "open_count": open_count,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "win_rate": win_rate,
        "expectancy_r": expectancy,
        "expectancy_ci_low": ci_low,
        "expectancy_ci_high": ci_high,
        "avg_rr": avg_rr,
    }
