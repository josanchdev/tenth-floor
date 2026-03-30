-- Rename killed_btc_corr_gate → killed_leader_corr_gate
-- (generalized from BTC-specific to per-asset-class leader gates in V4)
-- Only runs if the old column still exists (skipped on fresh DBs).
ALTER TABLE pipeline_runs RENAME COLUMN killed_btc_corr_gate TO killed_leader_corr_gate;
