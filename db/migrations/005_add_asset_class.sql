-- Add asset_class column to signals and pipeline_runs for V4 multi-asset support.
-- Defaults to 'crypto' for all existing data (V3 was crypto-only).

ALTER TABLE signals ADD COLUMN asset_class TEXT NOT NULL DEFAULT 'crypto';
ALTER TABLE pipeline_runs ADD COLUMN asset_class TEXT;
