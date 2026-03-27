-- Add killed_sector_cap column to pipeline_runs for sector diversity tracking.
-- SQLite ALTER TABLE only supports ADD COLUMN, which is fine here.
ALTER TABLE pipeline_runs ADD COLUMN killed_sector_cap INTEGER NOT NULL DEFAULT 0;
