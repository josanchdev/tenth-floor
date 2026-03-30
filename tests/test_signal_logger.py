"""Unit tests for SignalLogger — SQLite signal persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tenth_floor.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SetupAction,
    SignalDirection,
)
from tenth_floor.db.signal_logger import SignalLogger


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_signals.db"


@pytest.fixture
def logger(db_path: Path) -> SignalLogger:
    return SignalLogger(db_path=db_path)


@pytest.fixture
def approved_entry() -> PlaybookEntry:
    return PlaybookEntry(
        symbol="BTCUSDT",
        timeframe="1d",
        report_date="2026-03-20",
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
        strategy_rationale="EMA alignment with volume support.",
        rank=1,
    )


@pytest.fixture
def rejected_entry() -> PlaybookEntry:
    return PlaybookEntry(
        symbol="ETHUSDT",
        timeframe="1d",
        report_date="2026-03-20",
        verdict=PlaybookVerdict.REJECTED,
        verdict_reasoning="Confidence too low.",
        direction=SignalDirection.LONG,
        action=SetupAction.BUY,
        entry_zone_low=3400.0,
        entry_zone_high=3440.0,
        stop_loss=3300.0,
        take_profit=3600.0,
        reward_risk_ratio=2.0,
        confidence_score=0.55,
        conviction="none",
        suggested_risk_pct=0.0,
        rank=2,
    )


class TestSignalLogger:
    def test_schema_applied_on_init(self, db_path: Path) -> None:
        """DB file is created and schema is applied."""
        SignalLogger(db_path=db_path)
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "signals" in tables

    def test_log_approved_signal(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """Approved entry is inserted with status PENDING."""
        signal_id = logger.log(approved_entry)

        assert signal_id is not None
        assert signal_id.startswith("BTCUSDT_2026-03-20_")

        row = logger.get_signal(signal_id)
        assert row is not None
        assert row["pair"] == "BTCUSDT"
        assert row["status"] == "PENDING"
        assert row["conviction"] == "high"
        assert row["confidence_score"] == 0.82
        assert row["entry_low"] == 61690.0
        assert row["entry_high"] == 62310.0
        assert row["stop_loss"] == 61090.0
        assert row["take_profit"] == 63510.0

    def test_log_rejected_signal_skipped(self, logger: SignalLogger, rejected_entry: PlaybookEntry) -> None:
        """Rejected entry is silently skipped."""
        result = logger.log(rejected_entry)
        assert result is None
        assert logger.open_signal_count() == 0

    def test_open_signal_count(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """Count includes both PENDING and OPEN signals."""
        logger.log(approved_entry)
        assert logger.open_signal_count() == 1

        # Log another
        entry2 = approved_entry.model_copy(update={"symbol": "ETHUSDT"})
        signal_id = logger.log(entry2)

        # Transition one to OPEN
        logger.update_signal(signal_id, status="OPEN")
        assert logger.open_signal_count() == 2

    def test_get_active_signals(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """Returns PENDING and OPEN signals, not resolved ones."""
        id1 = logger.log(approved_entry)
        entry2 = approved_entry.model_copy(update={"symbol": "ETHUSDT"})
        id2 = logger.log(entry2)

        # Resolve one
        logger.update_signal(id1, status="HIT_TP", outcome_price=63510.0)

        active = logger.get_active_signals()
        assert len(active) == 1
        assert active[0]["signal_id"] == id2

    def test_update_signal(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """update_signal modifies specific fields."""
        signal_id = logger.log(approved_entry)

        logger.update_signal(
            signal_id,
            status="HIT_TP",
            outcome_price=63510.0,
            outcome_date="2026-03-22T12:00:00+00:00",
            max_adverse_excursion=61800.0,
            max_favorable_excursion=63510.0,
        )

        row = logger.get_signal(signal_id)
        assert row["status"] == "HIT_TP"
        assert row["outcome_price"] == 63510.0
        assert row["max_adverse_excursion"] == 61800.0

    def test_update_rejects_bad_columns(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """Cannot update arbitrary columns via update_signal."""
        signal_id = logger.log(approved_entry)

        with pytest.raises(ValueError, match="Cannot update columns"):
            logger.update_signal(signal_id, pair="HACKED")

    def test_log_with_langfuse_trace(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """Langfuse trace ID is stored when provided."""
        signal_id = logger.log(approved_entry, langfuse_trace_id="trace-abc-123")

        row = logger.get_signal(signal_id)
        assert row["langfuse_trace_id"] == "trace-abc-123"
