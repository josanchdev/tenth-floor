"""Unit tests for check_outcomes — candle walk, TP/SL detection, expiry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tenth_floor.check_outcomes import _process_signal
from tenth_floor.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SignalDirection,
)
from tenth_floor.db.signal_logger import SignalLogger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candles(
    rows: list[tuple[float, float, float, float]],
    start_ts: int = 1710374400000,  # 2024-03-14 00:00 UTC
    interval_ms: int = 14_400_000,  # 4h
) -> pd.DataFrame:
    """Create a candles DataFrame from (open, high, low, close) tuples."""
    data = []
    for i, (o, h, low, c) in enumerate(rows):
        data.append({
            "timestamp": start_ts + i * interval_ms,
            "open": o, "high": h, "low": low, "close": c, "volume": 1000.0,
        })
    return pd.DataFrame(data)


def _make_signal(
    status: str = "OPEN",
    created_at: str = "2024-03-14T00:00:00+00:00",
    entry_price: float = 62000.0,
    stop_loss: float = 61090.0,
    take_profit: float = 63510.0,
    **overrides: object,
) -> dict:
    """Create a signal dict matching the DB row shape."""
    sig = {
        "signal_id": "TEST_001",
        "created_at": created_at,
        "report_date": "2024-03-14",
        "pair": "BTCUSDT",
        "timeframe": "4h",
        "direction": "long",
        "conviction": "high",
        "confidence_score": 0.82,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reward_risk": 2.0,
        "suggested_risk_pct": 0.02,
        "rationale": "Test signal.",
        "status": status,
        "outcome_price": None,
        "outcome_date": None,
        "max_adverse_excursion": None,
        "max_favorable_excursion": None,
        "langfuse_trace_id": None,
    }
    sig.update(overrides)
    return sig


# ---------------------------------------------------------------------------
# _process_signal tests
# ---------------------------------------------------------------------------


class TestProcessSignal:
    def test_open_to_tp_hit(self) -> None:
        """OPEN → HIT_TP when candle high reaches take-profit."""
        signal = _make_signal()
        candles = _make_candles([
            (62500.0, 63000.0, 62200.0, 62800.0),  # normal
            (62800.0, 63600.0, 62700.0, 63500.0),  # TP hit
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_TP"
        assert result["outcome_price"] == 63510.0

    def test_open_to_sl_hit(self) -> None:
        """OPEN → HIT_SL when candle low reaches stop-loss."""
        signal = _make_signal()
        candles = _make_candles([
            (62000.0, 62200.0, 61000.0, 61500.0),
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_SL"
        assert result["outcome_price"] == 61090.0

    def test_same_candle_sl_wins(self) -> None:
        """If both TP and SL hit in same candle, SL wins (conservative)."""
        signal = _make_signal()
        candles = _make_candles([
            (62000.0, 63600.0, 61000.0, 62500.0),
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_SL"
        assert result["outcome_price"] == 61090.0

    def test_mae_mfe_tracking(self) -> None:
        """MAE and MFE are tracked correctly during candle walk."""
        signal = _make_signal()
        candles = _make_candles([
            (62300.0, 63000.0, 61500.0, 62800.0),  # dip then rise
            (62800.0, 63200.0, 62000.0, 63100.0),  # higher
            (63100.0, 63600.0, 62500.0, 63500.0),  # TP hit
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_TP"
        assert result["max_adverse_excursion"] == 61500.0
        assert result["max_favorable_excursion"] == 63600.0

    def test_expiry_open_14_days(self) -> None:
        """OPEN signal expires after 14 days without TP or SL hit."""
        signal = _make_signal(created_at="2024-03-01T00:00:00+00:00")
        late_ts = int(datetime(2024, 3, 16, 0, 0, tzinfo=UTC).timestamp() * 1000)
        candles = _make_candles(
            [(62000.0, 62500.0, 61500.0, 62300.0)],
            start_ts=late_ts,
        )

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "EXPIRED"

    def test_no_candles_no_change(self) -> None:
        """If no candles exist after signal creation, no change (unless expired)."""
        now = datetime.now(UTC).isoformat()
        signal = _make_signal(created_at=now)
        candles = _make_candles([])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result is None


# ---------------------------------------------------------------------------
# Integration test with real DB
# ---------------------------------------------------------------------------


class TestCheckOutcomesIntegration:
    def test_full_flow_with_db(self, tmp_path: Path) -> None:
        """End-to-end: log signal → resolve → verify DB."""
        db_path = tmp_path / "test.db"
        sig_logger = SignalLogger(db_path=db_path)

        entry = PlaybookEntry(
            symbol="BTCUSDT",
            timeframe="1d",
            report_date="2024-03-14",
            verdict=PlaybookVerdict.APPROVED,
            verdict_reasoning="Strong setup.",
            direction=SignalDirection.LONG,
            entry_price=62000.0,
            stop_loss=61090.0,
            take_profit=63510.0,
            reward_risk_ratio=2.0,
            confidence_score=0.82,
            conviction="high",
            suggested_risk_pct=0.02,
            rationale="Test.",
            rank=1,
        )
        signal_id = sig_logger.log(entry)
        assert signal_id is not None

        row = sig_logger.get_signal(signal_id)
        assert row["status"] == "OPEN"

        sig_logger.update_signal(
            signal_id,
            status="HIT_TP",
            outcome_price=63510.0,
            outcome_date="2024-03-16T12:00:00+00:00",
            max_adverse_excursion=61800.0,
            max_favorable_excursion=63510.0,
        )

        row = sig_logger.get_signal(signal_id)
        assert row["status"] == "HIT_TP"
        assert row["outcome_price"] == 63510.0

        assert sig_logger.open_signal_count() == 0
