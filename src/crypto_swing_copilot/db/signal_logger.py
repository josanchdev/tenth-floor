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
from datetime import UTC, datetime
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
        """Apply DDL from ``db/schema.sql`` and run pending migrations."""
        schema_path = CONFIG_DIR.parent / "db" / "schema.sql"
        # Fallback: try project root
        if not schema_path.exists():
            schema_path = PROJECT_ROOT / "db" / "schema.sql"
        with open(schema_path, encoding="utf-8") as fh:
            ddl = fh.read()
        self._conn.executescript(ddl)
        self._conn.commit()
        self._run_migrations()

    def _run_migrations(self) -> None:
        """Apply any unapplied migrations from ``db/migrations/``.

        Migrations are numbered SQL files (``001_name.sql``, ``002_name.sql``).
        Applied migrations are tracked in a ``schema_migrations`` table.
        """
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  version TEXT PRIMARY KEY,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

        # Find migration files
        migrations_dir = CONFIG_DIR.parent / "db" / "migrations"
        if not migrations_dir.exists():
            migrations_dir = PROJECT_ROOT / "db" / "migrations"
        if not migrations_dir.exists():
            return

        applied = {
            row[0]
            for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        migration_files = sorted(migrations_dir.glob("*.sql"))
        for mf in migration_files:
            version = mf.stem  # e.g. "001_baseline"
            if version in applied:
                continue

            logger.info("Applying migration: %s", version)
            with open(mf, encoding="utf-8") as fh:
                sql = fh.read()

            # Strip SQL comments to check if there's real SQL to execute
            real_sql = "\n".join(
                line for line in sql.splitlines()
                if line.strip() and not line.strip().startswith("--")
            )
            if real_sql.strip():
                try:
                    self._conn.executescript(sql)
                except sqlite3.OperationalError as exc:
                    # Idempotent: ignore "duplicate column" from ALTER TABLE
                    # when schema.sql already includes the column (fresh DBs).
                    if "duplicate column" in str(exc):
                        logger.debug("Migration %s: column already exists — skipping", version)
                    else:
                        raise

            self._conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
            self._conn.commit()
            logger.info("Migration applied: %s", version)

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
        now = datetime.now(UTC).isoformat()

        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO signals (
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

        if cursor.rowcount == 0:
            logger.info(
                "Signal already exists for %s %s on %s — skipped duplicate",
                entry.symbol, entry.timeframe, entry.report_date,
            )
            return None

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

    def log_pipeline_run(self, run_date: str, funnel: object) -> None:
        """Persist pipeline funnel diagnostics.

        Parameters
        ----------
        run_date:
            ISO date string (YYYY-MM-DD).
        funnel:
            A ``FunnelTracker`` instance (or any object with matching attributes).
        """
        from crypto_swing_copilot.agents.base import _active_profile

        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_runs (
                run_date, created_at, pairs_analyzed,
                killed_trend_gate, killed_strategy_skip, killed_volume_gate,
                killed_rs_gate, killed_confidence_gate, killed_rr_gate,
                killed_btc_corr_gate, killed_sector_cap, killed_signal_cap,
                proposals_generated, approved, published, profile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date, now, funnel.pairs_analyzed,  # type: ignore[union-attr]
                funnel.killed_trend_gate, funnel.killed_strategy_skip,  # type: ignore[union-attr]
                funnel.killed_volume_gate, funnel.killed_rs_gate,  # type: ignore[union-attr]
                funnel.killed_confidence_gate, funnel.killed_rr_gate,  # type: ignore[union-attr]
                funnel.killed_btc_corr_gate, funnel.killed_sector_cap,  # type: ignore[union-attr]
                funnel.killed_signal_cap,  # type: ignore[union-attr]
                funnel.proposals_generated, funnel.approved,  # type: ignore[union-attr]
                funnel.published, _active_profile,  # type: ignore[union-attr]
            ),
        )
        self._conn.commit()
        logger.info("Pipeline run logged  date=%s", run_date)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> SignalLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
