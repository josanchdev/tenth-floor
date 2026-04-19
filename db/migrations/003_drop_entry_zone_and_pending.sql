-- 003_drop_entry_zone_and_pending — collapse entry zone to a single
-- entry_price and remove the PENDING status.
--
-- Rationale: the pipeline runs once a day, so a "wait for the candle to
-- enter the zone" model adds nothing — by the next run the situation has
-- moved on. Signals now open immediately at the snapshot price the
-- operator saw when they hit Run.
--
-- For an existing DB:
--   • Any leftover PENDING rows are stale by definition — promote them
--     to EXPIRED so the resolved-only stats stay consistent.
--   • Collapse entry_low/entry_high to a single entry_price by renaming
--     entry_low (the more conservative fill for backfill purposes).
--   • Drop the now-meaningless entry_high and entered_at columns.
--
-- For a fresh DB the post-migration schema is already in place; SQLite
-- will raise "no such column" on each ALTER and the migration runner
-- catches and marks the migration applied (see signal_logger
-- _run_migrations).

UPDATE signals SET status = 'EXPIRED' WHERE status = 'PENDING';

ALTER TABLE signals RENAME COLUMN entry_low TO entry_price;
ALTER TABLE signals DROP COLUMN entry_high;
ALTER TABLE signals DROP COLUMN entered_at;
