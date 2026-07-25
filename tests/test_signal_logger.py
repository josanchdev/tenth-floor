"""Unit tests for SignalLogger — SQLite signal persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tenth_floor.data.models import (
    PlaybookEntry,
    PlaybookVerdict,
    SignalDirection,
)
from tenth_floor.db.signal_logger import SignalLogger

# Fixtures must stay inside the lookback window that get_recent_signals()
# applies to report_date, so the date has to be relative to now. A hardcoded
# date silently ages out of the window and fails the suite months later.
TODAY = datetime.now(UTC).date().isoformat()


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
        report_date=TODAY,
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
        rationale="EMA alignment with volume support.",
        rank=1,
    )


@pytest.fixture
def rejected_entry() -> PlaybookEntry:
    return PlaybookEntry(
        symbol="ETHUSDT",
        timeframe="1d",
        report_date=TODAY,
        verdict=PlaybookVerdict.REJECTED,
        verdict_reasoning="Confidence too low.",
        direction=SignalDirection.LONG,
        entry_price=3420.0,
        stop_loss=3300.0,
        take_profit=3600.0,
        reward_risk_ratio=2.0,
        confidence_score=0.55,
        conviction="standard",
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
        """Approved entry is inserted with status OPEN."""
        signal_id = logger.log(approved_entry)

        assert signal_id is not None
        assert signal_id.startswith(f"BTCUSDT_{TODAY}_")

        row = logger.get_signal(signal_id)
        assert row is not None
        assert row["pair"] == "BTCUSDT"
        assert row["status"] == "OPEN"
        assert row["conviction"] == "high"
        assert row["confidence_score"] == 0.82
        assert row["entry_price"] == 62000.0
        assert row["stop_loss"] == 61090.0
        assert row["take_profit"] == 63510.0

    def test_log_rejected_signal_skipped(self, logger: SignalLogger, rejected_entry: PlaybookEntry) -> None:
        """Rejected entry is silently skipped."""
        result = logger.log(rejected_entry)
        assert result is None
        assert logger.open_signal_count() == 0

    def test_open_signal_count(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """Count includes OPEN signals."""
        logger.log(approved_entry)
        assert logger.open_signal_count() == 1

        # Log another
        entry2 = approved_entry.model_copy(update={"symbol": "ETHUSDT"})
        logger.log(entry2)
        assert logger.open_signal_count() == 2

    def test_get_active_signals(self, logger: SignalLogger, approved_entry: PlaybookEntry) -> None:
        """Returns OPEN signals, not resolved ones."""
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


class TestSignalTier:
    """Tier column — PUBLISHED (top-N, tracked) vs SESSION (runner-up, 24h TTL)."""

    def test_default_tier_is_published(
        self, logger: SignalLogger, approved_entry: PlaybookEntry
    ) -> None:
        signal_id = logger.log(approved_entry)
        assert logger.get_signal(signal_id)["tier"] == "PUBLISHED"

    def test_log_session_tier(
        self, logger: SignalLogger, approved_entry: PlaybookEntry
    ) -> None:
        signal_id = logger.log(approved_entry, tier="SESSION")
        assert logger.get_signal(signal_id)["tier"] == "SESSION"

    def test_invalid_tier_raises(
        self, logger: SignalLogger, approved_entry: PlaybookEntry
    ) -> None:
        with pytest.raises(ValueError, match="Invalid tier"):
            logger.log(approved_entry, tier="DRAFT")

    def test_get_active_signals_excludes_session(
        self, logger: SignalLogger, approved_entry: PlaybookEntry
    ) -> None:
        """Outcome checker must never see session signals."""
        logger.log(approved_entry, tier="PUBLISHED")
        eth = approved_entry.model_copy(update={"symbol": "ETHUSDT"})
        logger.log(eth, tier="SESSION")

        active = logger.get_active_signals()
        assert len(active) == 1
        assert active[0]["pair"] == "BTCUSDT"

    def test_open_signal_count_excludes_session(
        self, logger: SignalLogger, approved_entry: PlaybookEntry
    ) -> None:
        logger.log(approved_entry, tier="PUBLISHED")
        eth = approved_entry.model_copy(update={"symbol": "ETHUSDT"})
        logger.log(eth, tier="SESSION")
        assert logger.open_signal_count() == 1

    def test_get_recent_signals_filters_by_tier(
        self, logger: SignalLogger, approved_entry: PlaybookEntry
    ) -> None:
        logger.log(approved_entry, tier="PUBLISHED")
        eth = approved_entry.model_copy(update={"symbol": "ETHUSDT"})
        logger.log(eth, tier="SESSION")

        published = logger.get_recent_signals(lookback_days=90, tier="PUBLISHED")
        session = logger.get_recent_signals(lookback_days=90, tier="SESSION")
        assert {r["pair"] for r in published} == {"BTCUSDT"}
        assert {r["pair"] for r in session} == {"ETHUSDT"}

    def test_session_age_filter_drops_old_session_rows(
        self, logger: SignalLogger, approved_entry: PlaybookEntry
    ) -> None:
        """Session rows older than 24h disappear from default queries."""
        from datetime import UTC, datetime, timedelta

        logger.log(approved_entry, tier="PUBLISHED")
        eth = approved_entry.model_copy(update={"symbol": "ETHUSDT"})
        session_id = logger.log(eth, tier="SESSION")

        old = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
        logger._conn.execute(
            "UPDATE signals SET created_at = ? WHERE signal_id = ?",
            (old, session_id),
        )
        logger._conn.commit()

        default = logger.get_recent_signals(lookback_days=90)
        assert {r["pair"] for r in default} == {"BTCUSDT"}

        with_expired = logger.get_recent_signals(
            lookback_days=90, include_expired_session=True
        )
        assert {r["pair"] for r in with_expired} == {"BTCUSDT", "ETHUSDT"}
