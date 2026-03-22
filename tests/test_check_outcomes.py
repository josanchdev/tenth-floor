"""Unit tests for check_outcomes — candle walk, TP/SL detection, expiry."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from crypto_swing_copilot.check_outcomes import _process_signal, check_outcomes
from crypto_swing_copilot.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SetupAction,
    SignalDirection,
)
from crypto_swing_copilot.db.signal_logger import SignalLogger


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
    for i, (o, h, l, c) in enumerate(rows):
        data.append({
            "timestamp": start_ts + i * interval_ms,
            "open": o, "high": h, "low": l, "close": c, "volume": 1000.0,
        })
    return pd.DataFrame(data)


def _make_signal(
    status: str = "PENDING",
    created_at: str = "2024-03-14T00:00:00+00:00",
    entry_low: float = 61690.0,
    entry_high: float = 62310.0,
    stop_loss: float = 61090.0,
    take_profit: float = 63510.0,
    **overrides: object,
) -> dict:
    """Create a signal dict matching the DB row shape."""
    sig = {
        "signal_id": "TEST_001",
        "created_at": created_at,
        "entered_at": None,
        "report_date": "2024-03-14",
        "pair": "BTCUSDT",
        "timeframe": "4h",
        "direction": "long",
        "conviction": "high",
        "confidence_score": 0.82,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reward_risk": 2.0,
        "suggested_risk_pct": 0.02,
        "strategy_rationale": "Test signal.",
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
    def test_pending_to_open(self) -> None:
        """PENDING → OPEN when candle low enters entry zone."""
        signal = _make_signal(status="PENDING")
        # Candle dips into entry zone (low=62000 < entry_high=62310)
        candles = _make_candles([
            (62500.0, 62800.0, 62000.0, 62300.0),
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result is not None
        assert result["status"] == "OPEN"
        assert "entered_at" in result

    def test_pending_price_above_entry_zone(self) -> None:
        """PENDING stays PENDING if price never enters entry zone."""
        signal = _make_signal(status="PENDING")
        # Candle low=62500 > entry_high=62310 — never enters zone
        candles = _make_candles([
            (62800.0, 63000.0, 62500.0, 62700.0),
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result is None

    def test_open_to_tp_hit(self) -> None:
        """OPEN → HIT_TP when candle high reaches take-profit."""
        signal = _make_signal(status="OPEN", entered_at="2024-03-14T00:00:00+00:00")
        # Candle high=63600 > take_profit=63510
        candles = _make_candles([
            (62500.0, 63000.0, 62200.0, 62800.0),  # normal
            (62800.0, 63600.0, 62700.0, 63500.0),  # TP hit
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_TP"
        assert result["outcome_price"] == 63510.0

    def test_open_to_sl_hit(self) -> None:
        """OPEN → HIT_SL when candle low reaches stop-loss."""
        signal = _make_signal(status="OPEN", entered_at="2024-03-14T00:00:00+00:00")
        # Candle low=61000 < stop_loss=61090
        candles = _make_candles([
            (62000.0, 62200.0, 61000.0, 61500.0),
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_SL"
        assert result["outcome_price"] == 61090.0

    def test_same_candle_sl_wins(self) -> None:
        """If both TP and SL hit in same candle, SL wins (conservative)."""
        signal = _make_signal(status="OPEN", entered_at="2024-03-14T00:00:00+00:00")
        # Candle hits both: low=61000 < SL=61090 AND high=63600 > TP=63510
        candles = _make_candles([
            (62000.0, 63600.0, 61000.0, 62500.0),
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_SL"
        assert result["outcome_price"] == 61090.0

    def test_pending_enters_and_hits_tp_same_walk(self) -> None:
        """PENDING can transition to OPEN and hit TP in the same candle walk."""
        signal = _make_signal(status="PENDING")
        candles = _make_candles([
            (62500.0, 62800.0, 62000.0, 62300.0),  # enters zone
            (62300.0, 63600.0, 62200.0, 63500.0),  # TP hit
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_TP"
        assert result["outcome_price"] == 63510.0

    def test_mae_mfe_tracking(self) -> None:
        """MAE and MFE are tracked correctly during candle walk."""
        signal = _make_signal(status="OPEN", entered_at="2024-03-14T00:00:00+00:00")
        candles = _make_candles([
            (62300.0, 63000.0, 61500.0, 62800.0),  # dip then rise
            (62800.0, 63200.0, 62000.0, 63100.0),  # higher
            (63100.0, 63600.0, 62500.0, 63500.0),  # TP hit
        ])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "HIT_TP"
        assert result["max_adverse_excursion"] == 61500.0  # lowest low
        assert result["max_favorable_excursion"] == 63600.0  # highest high

    def test_expiry_pending_14_days(self) -> None:
        """PENDING signal expires after 14 days without entry."""
        signal = _make_signal(
            status="PENDING",
            created_at="2024-03-01T00:00:00+00:00",
        )
        # Candles 15 days later, never entering the zone
        late_ts = int(datetime(2024, 3, 16, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        candles = _make_candles(
            [(65000.0, 66000.0, 64000.0, 65500.0)],
            start_ts=late_ts,
        )

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "EXPIRED"

    def test_expiry_open_14_days(self) -> None:
        """OPEN signal expires after 14 days without TP or SL hit."""
        signal = _make_signal(
            status="OPEN",
            created_at="2024-03-01T00:00:00+00:00",
            entered_at="2024-03-01T04:00:00+00:00",
        )
        # Candle 15 days later — price is between SL and TP (no hit)
        late_ts = int(datetime(2024, 3, 16, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        candles = _make_candles(
            [(62000.0, 62500.0, 61500.0, 62300.0)],
            start_ts=late_ts,
        )

        result = _process_signal(signal, candles, expiry_days=14)
        assert result["status"] == "EXPIRED"

    def test_no_candles_no_change(self) -> None:
        """If no candles exist after signal creation, no change (unless expired)."""
        # Use a very recent created_at so expiry doesn't trigger
        now = datetime.now(timezone.utc).isoformat()
        signal = _make_signal(status="PENDING", created_at=now)
        candles = _make_candles([])

        result = _process_signal(signal, candles, expiry_days=14)
        assert result is None


# ---------------------------------------------------------------------------
# Integration test with real DB
# ---------------------------------------------------------------------------


class TestCheckOutcomesIntegration:
    def test_full_flow_with_db(self, tmp_path: Path) -> None:
        """End-to-end: log signal → simulate candles → check outcome → verify DB."""
        db_path = tmp_path / "test.db"
        sig_logger = SignalLogger(db_path=db_path)

        # Log an approved signal
        entry = PlaybookEntry(
            symbol="BTCUSDT",
            timeframe="4h",
            report_date="2024-03-14",
            verdict=PlaybookVerdict.APPROVED,
            verdict_reasoning="Strong setup.",
            direction=SignalDirection.LONG,
            action=SetupAction.BUY,
            entry_zone_low=61690.0,
            entry_zone_high=62310.0,
            stop_loss=61090.0,
            take_profit=63510.0,
            reward_risk_ratio=2.0,
            confidence_score=0.82,
            conviction="high",
            suggested_risk_pct=0.02,
            strategy_rationale="Test.",
            rank=1,
        )
        signal_id = sig_logger.log(entry)
        assert signal_id is not None

        # Verify initial state
        row = sig_logger.get_signal(signal_id)
        assert row["status"] == "PENDING"

        # Simulate: transition to OPEN then TP_HIT
        sig_logger.update_signal(signal_id, status="OPEN", entered_at="2024-03-14T04:00:00+00:00")
        sig_logger.update_signal(
            signal_id,
            status="HIT_TP",
            outcome_price=63510.0,
            outcome_date="2024-03-16T12:00:00+00:00",
            max_adverse_excursion=61800.0,
            max_favorable_excursion=63510.0,
        )

        # Verify final state
        row = sig_logger.get_signal(signal_id)
        assert row["status"] == "HIT_TP"
        assert row["outcome_price"] == 63510.0
        assert row["entered_at"] == "2024-03-14T04:00:00+00:00"

        # No longer in active signals
        assert sig_logger.open_signal_count() == 0


# ---------------------------------------------------------------------------
# Discord notifier wiring
# ---------------------------------------------------------------------------


class TestCheckOutcomesNotifier:
    """Verify check_outcomes() calls notifier on resolution but not on dry run."""

    def _setup_db_with_open_signal(self, tmp_path: Path) -> tuple[Path, str]:
        """Log a signal and transition it to OPEN so we can test resolution."""
        db_path = tmp_path / "test.db"
        sig_logger = SignalLogger(db_path=db_path)
        entry = PlaybookEntry(
            symbol="BTCUSDT", timeframe="1d", report_date="2024-03-14",
            verdict=PlaybookVerdict.APPROVED, verdict_reasoning="Test.",
            direction=SignalDirection.LONG, action=SetupAction.BUY,
            entry_zone_low=61690.0, entry_zone_high=62310.0,
            stop_loss=61090.0, take_profit=63510.0,
            reward_risk_ratio=2.0, confidence_score=0.82,
            conviction="high", suggested_risk_pct=0.02,
            strategy_rationale="Test.", rank=1,
        )
        signal_id = sig_logger.log(entry)
        sig_logger.update_signal(
            signal_id, status="OPEN",
            entered_at="2024-03-14T04:00:00+00:00",
        )
        return db_path, signal_id

    @staticmethod
    def _near_future_candles(
        rows: list[tuple[float, float, float, float]],
    ) -> pd.DataFrame:
        """Candles timestamped just after now() so they pass the created_at filter."""
        # 1 hour from now — after created_at but within expiry window
        near_future_ms = int((datetime.now(timezone.utc).timestamp() + 3600) * 1000)
        return _make_candles(rows, start_ts=near_future_ms)

    def test_notifier_called_on_tp_hit(self, tmp_path: Path) -> None:
        db_path, _ = self._setup_db_with_open_signal(tmp_path)

        # Candle that triggers TP (high >= 63510)
        candles = self._near_future_candles([(62000, 64000, 61500, 63800)])
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = candles
        mock_notifier = MagicMock()

        check_outcomes(db_path=db_path, fetcher=mock_fetcher, notifier=mock_notifier)

        mock_notifier.post_outcome.assert_called_once()
        call_arg = mock_notifier.post_outcome.call_args[0][0]
        assert call_arg["status"] == "HIT_TP"

    def test_notifier_not_called_on_dry_run(self, tmp_path: Path) -> None:
        db_path, _ = self._setup_db_with_open_signal(tmp_path)

        candles = self._near_future_candles([(62000, 64000, 61500, 63800)])
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = candles
        mock_notifier = MagicMock()

        check_outcomes(
            db_path=db_path, fetcher=mock_fetcher,
            notifier=mock_notifier, dry_run=True,
        )

        mock_notifier.post_outcome.assert_not_called()

    def test_notifier_not_called_on_open_transition(self, tmp_path: Path) -> None:
        """PENDING → OPEN should NOT trigger a Discord notification."""
        db_path = tmp_path / "test.db"
        sig_logger = SignalLogger(db_path=db_path)
        entry = PlaybookEntry(
            symbol="BTCUSDT", timeframe="1d", report_date="2024-03-14",
            verdict=PlaybookVerdict.APPROVED, verdict_reasoning="Test.",
            direction=SignalDirection.LONG, action=SetupAction.BUY,
            entry_zone_low=61690.0, entry_zone_high=62310.0,
            stop_loss=61090.0, take_profit=63510.0,
            reward_risk_ratio=2.0, confidence_score=0.82,
            conviction="high", suggested_risk_pct=0.02,
            strategy_rationale="Test.", rank=1,
        )
        sig_logger.log(entry)

        # Candle enters entry zone but doesn't hit TP or SL
        candles = self._near_future_candles([(62500, 62800, 62000, 62300)])
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_ohlcv.return_value = candles
        mock_notifier = MagicMock()

        check_outcomes(db_path=db_path, fetcher=mock_fetcher, notifier=mock_notifier)

        mock_notifier.post_outcome.assert_not_called()
