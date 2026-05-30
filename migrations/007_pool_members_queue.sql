-- 007_pool_members_queue.sql
-- Per-member pre-draft queue. Ordered list of team_ref UUIDs the member
-- wants to draft, kept private to that member. Used by the draft room's
-- "My Queue" panel; pick happens via the existing /draft/pick route.
ALTER TABLE pool_members
    ADD COLUMN IF NOT EXISTS queue JSONB NOT NULL DEFAULT '[]'::jsonb;
