-- 008_auction_v2.sql
-- Post-snake mini-auction. One absolute deadline (auction_closes_at) applies
-- to all teams; bids accepted until then; cron settles at deadline.

-- Use team_ref to match the draft_picks + queue convention; keep the legacy
-- NBA-only columns nullable so old rows still validate.
ALTER TABLE auction_bids
    ADD COLUMN IF NOT EXISTS team_ref UUID REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE auction_bids ALTER COLUMN nba_team_id DROP NOT NULL;

-- Speeds up the "current high bid per team" query the bid endpoint runs on
-- every place_bid + every page load.
CREATE INDEX IF NOT EXISTS idx_auction_bids_pool_team_amount
    ON auction_bids(pool_id, team_ref, bid_amount DESC);

-- Pool-level auction config: when bidding closes, and the bid step.
ALTER TABLE pools
    ADD COLUMN IF NOT EXISTS auction_closes_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS auction_bid_increment INT NOT NULL DEFAULT 25;

-- Realtime delivery + RLS off (same pattern that draft_picks uses).
-- Wrap the publication add so re-running this file doesn't error when the
-- table is already a member.
DO $pub$ BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE auction_bids;
EXCEPTION WHEN duplicate_object THEN NULL;
END $pub$;
ALTER TABLE auction_bids DISABLE ROW LEVEL SECURITY;
