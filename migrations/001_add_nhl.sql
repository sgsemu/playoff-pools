-- Migration: Add NHL support
-- Run this in Supabase SQL Editor

-- 1. Add NHL teams table (same structure as nba_teams)
CREATE TABLE IF NOT EXISTS nhl_teams (
    id INT PRIMARY KEY,
    name TEXT NOT NULL,
    abbreviation TEXT NOT NULL,
    conference TEXT NOT NULL CHECK (conference IN ('East', 'West')),
    seed INT,
    is_eliminated BOOLEAN NOT NULL DEFAULT FALSE,
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0
);

-- 2. Add league + team_id columns to draft_picks
ALTER TABLE draft_picks ADD COLUMN IF NOT EXISTS league TEXT NOT NULL DEFAULT 'nba';
ALTER TABLE draft_picks ADD COLUMN IF NOT EXISTS team_id INT;
UPDATE draft_picks SET team_id = nba_team_id WHERE team_id IS NULL;

-- 3. Add league + team_id columns to auction_bids
ALTER TABLE auction_bids ADD COLUMN IF NOT EXISTS league TEXT NOT NULL DEFAULT 'nba';
ALTER TABLE auction_bids ADD COLUMN IF NOT EXISTS team_id INT;
UPDATE auction_bids SET team_id = nba_team_id WHERE team_id IS NULL;

-- 4. Add NHL game results support
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS league TEXT NOT NULL DEFAULT 'nba';

-- 5. Enable realtime on nhl_teams
ALTER PUBLICATION supabase_realtime ADD TABLE nhl_teams;
