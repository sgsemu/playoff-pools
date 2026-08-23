-- 013_game_complete.sql
-- Completion flag so game_results can hold both finished games and the
-- upcoming schedule (scheduled rows synced with home_score/away_score 0/0).
-- Default true so all EXISTING rows -- which today are only ever synced when
-- already complete (sync.py's `if not game["is_complete"]: continue` skip) --
-- remain "complete" and every current scoring/resolution read keeps behaving
-- exactly as it does today, with no backfill required.
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS is_complete BOOLEAN NOT NULL DEFAULT true;
