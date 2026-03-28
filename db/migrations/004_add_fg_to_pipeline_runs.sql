-- Add Fear & Greed value to pipeline_runs for tweet drafter context.
ALTER TABLE pipeline_runs ADD COLUMN fear_greed_value INTEGER;
