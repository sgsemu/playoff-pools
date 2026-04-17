-- 002_add_draft_position.sql
-- Adds draft_position to pool_members so the pool creator can override
-- the default join-order snake draft.

ALTER TABLE pool_members
    ADD COLUMN IF NOT EXISTS draft_position INT NULL;

CREATE INDEX IF NOT EXISTS idx_pool_members_draft_position
    ON pool_members(pool_id, draft_position);
