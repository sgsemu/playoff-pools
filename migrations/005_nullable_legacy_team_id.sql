-- 005_nullable_legacy_team_id.sql
-- Registry-backed picks store team_ref (UUID) and leave the legacy integer
-- nba_team_id NULL. The base schema declared it NOT NULL, which blocks
-- non-NBA (e.g. World Cup) picks. Relax it on both picks and bids.
ALTER TABLE draft_picks  ALTER COLUMN nba_team_id DROP NOT NULL;
ALTER TABLE auction_bids ALTER COLUMN nba_team_id DROP NOT NULL;
