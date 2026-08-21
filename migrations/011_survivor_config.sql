-- 011_survivor_config.sql
-- Per-pool survivor ruleset (lock time, mercy week, buyback windows), seeded
-- with the HBK Dads' Survivor 2026 house rules at pool-creation time and read
-- by routes/survivor.py (pool.get("survivor_config")).
ALTER TABLE pools ADD COLUMN IF NOT EXISTS survivor_config JSONB NOT NULL DEFAULT '{}'::jsonb;

-- The original pools_type_check (from supabase_schema.sql) only allows
-- 'draft' | 'auction' | 'salary_cap' -- it predates the survivor pool type
-- and would reject every survivor pool insert. Widen it the same way
-- 008_auction_v2.sql widened pools_draft_status_check for 'auction'.
ALTER TABLE pools DROP CONSTRAINT IF EXISTS pools_type_check;
ALTER TABLE pools ADD CONSTRAINT pools_type_check
    CHECK (type IN ('draft', 'auction', 'salary_cap', 'survivor'));
