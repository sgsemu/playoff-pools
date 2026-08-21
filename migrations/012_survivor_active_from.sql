-- 012_survivor_active_from.sql
-- Watermark: the earliest week an entry is "in play". Grading must never
-- eliminate an entry for a week earlier than the week it (re-)entered, so a
-- buyback, a commissioner reinstate, or a mid-season join survives the
-- re-resolution that runs on every ESPN sync. Defaults to 1 so existing rows
-- keep being graded from week 1 (they were created at the season's start).
ALTER TABLE survivor_entries ADD COLUMN IF NOT EXISTS active_from_week INT NOT NULL DEFAULT 1;
