-- 004_notion_page_id — track the Notion journal page for each signal.
-- Allows check_outcomes to auto-update Outcome + Post-mortem when a signal resolves.
ALTER TABLE signals ADD COLUMN notion_page_id TEXT;
