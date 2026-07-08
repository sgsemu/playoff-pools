-- 009_game_winner.sql
-- Record the declared winner of a completed game. Required for penalty-shootout
-- and extra-time knockouts, where home_score/away_score hold the tied regulation
-- score and can't reveal who advanced. NULL for draws and for legacy rows synced
-- before this column existed (the 010 backfill fills in known knockout results).
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS winner_team_id INT;
