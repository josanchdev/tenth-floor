"""
SignalLogger — persists approved signals to SQLite and queries signal state.

Writes approved ``PlaybookEntry`` records to ``data/playbook_history.db``.
The schema DDL is applied on first init via ``CREATE TABLE IF NOT EXISTS``.

Usage::

    from crypto_swing_copilot.db.signal_logger import SignalLogger

    logger = SignalLogger()
    signal_id = logger.log(approved_entry)
    count = logger.open_signal_count()
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from crypto_swing_copilot.config import CONFIG_DIR, PROJECT_ROOT
from crypto_swing_copilot.data.models import PlaybookEntry, PlaybookVerdict

logger = logging.getLogger(__name__)


def _load_db_config() -> dict:
    """Load database config from ``config/services.yaml``."""
    path = CONFIG_DIR / "services.yaml"
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("database", {})


class SignalLogger:
    """SQLite-backed signal persistence.

    Parameters
    ----------
    db_path:
        Override the database file path.  Defaults to the value in
        ``config/services.yaml`` → ``database.path``.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            cfg = _load_db_config()
            raw = cfg.get("path", "data/playbook_history.db")
            db_path = PROJECT_ROOT / raw
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()
        logger.info("SignalLogger initialised  db=%s", self._db_path)

    def _apply_schema(self) -> None:
        """Apply DDL from ``db/schema.sql``."""
        schema_path = CONFIG_DIR.parent / "db" / "schema.sql"
        # Fallback: try project root
        if not schema_path.exists():
            schema_path = PROJECT_ROOT / "db" / "schema.sql"
        with open(schema_path, encoding="utf-8") as fh:
            ddl = fh.read()
        self._conn.executescript(ddl)
        self._conn.commit()

    def log(
        self,
        entry: PlaybookEntry,
        langfuse_trace_id: str | None = None,
    ) -> str | None:
        """Insert an approved signal into the database.

        Rejected entries are silently skipped — only approved signals
        are persisted.

        Parameters
        ----------
        entry:
            A ``PlaybookEntry`` from RiskAgent.
        langfuse_trace_id:
            Optional Langfuse trace ID for observability linkage.

        Returns
        -------
        str | None
            The generated ``signal_id`` if inserted, or ``None`` if skipped.
        """
        if entry.verdict != PlaybookVerdict.APPROVED:
            logger.debug("Skipping non-approved entry: %s %s", entry.symbol, entry.verdict.value)
            return None

        signal_id = f"{entry.symbol}_{entry.report_date}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            """
            INSERT INTO signals (
                signal_id, created_at, report_date, pair, timeframe,
                direction, conviction, confidence_score,
                entry_low, entry_high, stop_loss, take_profit,
                reward_risk, suggested_risk_pct, strategy_rationale,
                status, langfuse_trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (
                signal_id, now, entry.report_date, entry.symbol, entry.timeframe,
                entry.direction.value, entry.conviction, entry.confidence_score,
                entry.entry_zone_low, entry.entry_zone_high,
                entry.stop_loss, entry.take_profit,
                entry.reward_risk_ratio, entry.suggested_risk_pct,
                entry.strategy_rationale,
                langfuse_trace_id,
            ),
        )
        self._conn.commit()

        logger.info(
            "Signal logged  id=%s  pair=%s  conviction=%s  confidence=%.2f",
            signal_id, entry.symbol, entry.conviction, entry.confidence_score,
        )
        return signal_id

    def open_signal_count(self) -> int:
        """Count signals with status PENDING or OPEN."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM signals WHERE status IN ('PENDING', 'OPEN')"
        ).fetchone()
        return row[0]

    def get_active_signals(self) -> list[dict]:
        """Return all PENDING and OPEN signals as dicts.

        Used by ``check_outcomes.py`` to determine which signals need
        candle-walking.
        """
        rows = self._conn.execute(
            "SELECT * FROM signals WHERE status IN ('PENDING', 'OPEN') ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]

    def update_signal(self, signal_id: str, **updates: object) -> None:
        """Update specific fields on a signal row.

        Parameters
        ----------
        signal_id:
            The signal to update.
        **updates:
            Column-value pairs to set, e.g.
            ``update_signal(id, status="HIT_TP", outcome_price=63000.0)``.
        """
        if not updates:
            return
        # Whitelist allowed columns
        allowed = {
            "status", "entered_at", "outcome_price", "outcome_date",
            "max_adverse_excursion", "max_favorable_excursion",
        }
        bad_keys = set(updates.keys()) - allowed
        if bad_keys:
            raise ValueError(f"Cannot update columns: {bad_keys}")

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [signal_id]

        self._conn.execute(
            f"UPDATE signals SET {set_clause} WHERE signal_id = ?",  # noqa: S608
            values,
        )
        self._conn.commit()
        logger.info("Signal updated  id=%s  %s", signal_id, updates)

    def get_signal(self, signal_id: str) -> dict | None:
        """Fetch a single signal by ID. Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
