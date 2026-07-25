"""
SignalLogger — persists approved signals to SQLite and queries signal state.

Writes approved ``PlaybookEntry`` records to ``data/playbook_history.db``.
The schema DDL is applied on first init via ``CREATE TABLE IF NOT EXISTS``.

Usage::

    from tenth_floor.db.signal_logger import SignalLogger

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
from typing import TYPE_CHECKING, Any

import yaml

from tenth_floor.config import CONFIG_DIR, PROJECT_ROOT
from tenth_floor.data.models import PlaybookEntry, PlaybookVerdict  # noqa: F401

if TYPE_CHECKING:
    # main.py imports this module, so this can only be a type-time import.
    from tenth_floor.main import FunnelTracker

logger = logging.getLogger(__name__)


def _iso_to_epoch(value: object) -> float:
    """Best-effort ISO-8601 → epoch seconds. Returns 0.0 on failure."""
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _load_db_config() -> dict:
    """Load database config from ``config/services.yaml``."""
    path = CONFIG_DIR / "services.yaml"
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    db_cfg: dict[Any, Any] = cfg.get("database", {})
    return db_cfg


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
                    # Idempotent: ignore harmless errors from ALTER TABLE
                    # when schema.sql already reflects the change (fresh DBs).
                    msg = str(exc).lower()
                    if "duplicate column" in msg or "no such column" in msg:
                        logger.debug("Migration %s: already applied to schema — skipping (%s)", version, exc)
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
        asset_class: str | None = None,
        tier: str = "PUBLISHED",
    ) -> str | None:
        """Insert an approved signal into the database.

        Rejected entries are silently skipped — only approved signals
        are persisted.

        Parameters
        ----------
        entry:
            A ``PlaybookEntry`` from the pipeline.
        langfuse_trace_id:
            Optional Langfuse trace ID for observability linkage.
        asset_class:
            Asset class label (e.g. ``"crypto"``, ``"equity"``).
        tier:
            ``"PUBLISHED"`` (top-N, tracked for outcomes) or
            ``"SESSION"`` (runner-up approval, dashboard-only, 24h TTL).

        Returns
        -------
        str | None
            The generated ``signal_id`` if inserted, or ``None`` if skipped.
        """
        if entry.verdict != PlaybookVerdict.APPROVED:
            logger.debug("Skipping non-approved entry: %s %s", entry.symbol, entry.verdict.value)
            return None
        if tier not in ("PUBLISHED", "SESSION"):
            raise ValueError(f"Invalid tier: {tier!r}")

        signal_id = f"{entry.symbol}_{entry.report_date}_{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC).isoformat()

        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO signals (
                signal_id, created_at, report_date, pair, timeframe,
                direction, conviction, confidence_score,
                entry_price, stop_loss, take_profit,
                reward_risk, suggested_risk_pct, strategy_rationale,
                status, langfuse_trace_id, asset_class, tier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, COALESCE(?, 'unknown'), ?)
            """,
            (
                signal_id, now, entry.report_date, entry.symbol, entry.timeframe,
                entry.direction.value, entry.conviction, entry.confidence_score,
                entry.entry_price,
                entry.stop_loss, entry.take_profit,
                entry.reward_risk_ratio, entry.suggested_risk_pct,
                entry.rationale,
                langfuse_trace_id,
                asset_class,
                tier,
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
        """Count published signals currently OPEN (awaiting resolution)."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM signals "
            "WHERE status = 'OPEN' AND tier = 'PUBLISHED'"
        ).fetchone()
        count: int = row[0]
        return count

    def get_active_signals(self) -> list[dict]:
        """Return all OPEN published signals as dicts.

        Session-tier signals are runner-ups that the dashboard surfaces
        for 24h but are never tracked for win/loss outcomes.
        """
        rows = self._conn.execute(
            "SELECT * FROM signals "
            "WHERE status = 'OPEN' AND tier = 'PUBLISHED' "
            "ORDER BY created_at"
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
            "status", "outcome_price", "outcome_date",
            "max_adverse_excursion", "max_favorable_excursion",
            "notion_page_id",
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

    def log_pipeline_run(
        self,
        run_date: str,
        funnel: FunnelTracker,
        fear_greed_value: int | None = None,
        macro_regime: str | None = None,
    ) -> None:
        """Persist pipeline funnel diagnostics.

        Parameters
        ----------
        run_date:
            ISO date string (YYYY-MM-DD).
        funnel:
            A ``FunnelTracker`` instance (or any object with matching attributes).
        fear_greed_value:
            Current Fear & Greed index value (for tweet drafter context).
        macro_regime:
            MacroAnalyst regime assessment (e.g. ``"risk_on"``).
        """
        from tenth_floor.agents.base import _active_profile

        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_runs (
                run_date, created_at, assets_in_universe, snapshots_built,
                pre_screen_passed, pre_screen_killed,
                trade_analyst_buy, trade_analyst_skip, trade_analyst_error,
                validation_passed, validation_failed,
                reviewer_approved, reviewer_rejected,
                signal_cap_killed, published, profile, fear_greed_value,
                macro_regime
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date, now,
                funnel.assets_in_universe, funnel.snapshots_built,
                funnel.pre_screen_passed, funnel.pre_screen_killed,
                funnel.trade_analyst_buy, funnel.trade_analyst_skip,
                funnel.trade_analyst_error,
                funnel.validation_passed, funnel.validation_failed,
                funnel.reviewer_approved, funnel.reviewer_rejected,
                funnel.signal_cap_killed, funnel.published,
                _active_profile, fear_greed_value, macro_regime,
            ),
        )
        self._conn.commit()
        logger.info("Pipeline run logged  date=%s  fg=%s  regime=%s", run_date, fear_greed_value, macro_regime)

    # ------------------------------------------------------------------
    # Read methods for tweet drafter
    # ------------------------------------------------------------------

    def get_pipeline_run(self, run_date: str) -> dict | None:
        """Fetch a single pipeline_runs row by date."""
        row = self._conn.execute(
            "SELECT * FROM pipeline_runs WHERE run_date = ?", (run_date,)
        ).fetchone()
        return dict(row) if row else None

    def get_recent_signals(
        self,
        lookback_days: int = 30,
        tier: str | None = None,
        include_expired_session: bool = False,
    ) -> list[dict]:
        """Fetch signals from the last N days.

        Parameters
        ----------
        lookback_days:
            Date window applied to ``report_date``.
        tier:
            ``"PUBLISHED"``, ``"SESSION"``, or ``None`` (both).
        include_expired_session:
            Session signals have a 24h dashboard TTL — by default
            session rows older than 24h are filtered out. Pass ``True``
            for archive views that need the full history.
        """
        rows = self._conn.execute(
            "SELECT * FROM signals WHERE report_date >= date('now', ?)",
            (f"-{lookback_days} days",),
        ).fetchall()
        result = [dict(row) for row in rows]

        if tier is not None:
            result = [r for r in result if r.get("tier") == tier]

        if not include_expired_session:
            cutoff = datetime.now(UTC).timestamp() - 24 * 3600
            result = [
                r for r in result
                if r.get("tier") != "SESSION"
                or _iso_to_epoch(r.get("created_at")) >= cutoff
            ]

        return result

    def get_last_published_date(self) -> str | None:
        """Return the most recent report_date with a published signal."""
        row = self._conn.execute(
            "SELECT report_date FROM signals ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> SignalLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
