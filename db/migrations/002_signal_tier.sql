-- 002_signal_tier — add tier column to signals.
--
-- PUBLISHED: top-N (max_daily_signals) approved signals. Tracked for
--            outcomes by check_outcomes.py and counted in /stats.
-- SESSION:   approved-but-not-top-N. Visible in dashboard for 24h
--            after the run, then archived. Not tracked for outcomes.
--
-- Existing rows are backfilled to PUBLISHED so historic stats are
-- preserved (every previously-logged signal was a published one).

ALTER TABLE signals ADD COLUMN tier TEXT NOT NULL DEFAULT 'PUBLISHED';
