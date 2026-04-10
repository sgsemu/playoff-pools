-- supabase_schema.sql
-- NBA Playoff Pools Platform

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users(email);

-- Pools
CREATE TABLE pools (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id UUID NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    league TEXT NOT NULL DEFAULT 'nba',
    type TEXT NOT NULL CHECK (type IN ('draft', 'auction', 'salary_cap')),
    invite_code TEXT UNIQUE NOT NULL,
    buy_in TEXT DEFAULT '',
    payout_description TEXT DEFAULT '',
    scoring_config JSONB NOT NULL DEFAULT '{}',
    auction_config JSONB DEFAULT '{}',
    draft_mode TEXT NOT NULL DEFAULT 'live' CHECK (draft_mode IN ('live', 'async')),
    draft_status TEXT NOT NULL DEFAULT 'pending' CHECK (draft_status IN ('pending', 'active', 'complete')),
    timer_seconds INT NOT NULL DEFAULT 60,
    season_year INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_pools_invite_code ON pools(invite_code);
CREATE INDEX idx_pools_creator ON pools(creator_id);

-- Pool Members
CREATE TABLE pool_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id),
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('creator', 'member')),
    total_points NUMERIC NOT NULL DEFAULT 0,
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(pool_id, user_id)
);

CREATE INDEX idx_pool_members_pool ON pool_members(pool_id);
CREATE INDEX idx_pool_members_user ON pool_members(user_id);

-- Draft Picks (pool type: draft)
CREATE TABLE draft_picks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    member_id UUID NOT NULL REFERENCES pool_members(id),
    nba_team_id INT NOT NULL,
    pick_order INT NOT NULL,
    round INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_draft_picks_pool ON draft_picks(pool_id);

-- Auction Bids (pool type: auction)
CREATE TABLE auction_bids (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    member_id UUID NOT NULL REFERENCES pool_members(id),
    nba_team_id INT NOT NULL,
    bid_amount NUMERIC NOT NULL,
    is_winning_bid BOOLEAN NOT NULL DEFAULT FALSE,
    bid_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_auction_bids_pool ON auction_bids(pool_id);
CREATE INDEX idx_auction_bids_team ON auction_bids(pool_id, nba_team_id);

-- Salary Rosters (pool type: salary_cap)
CREATE TABLE salary_rosters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    member_id UUID NOT NULL REFERENCES pool_members(id),
    nba_player_id INT NOT NULL,
    salary NUMERIC NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    total_points NUMERIC NOT NULL DEFAULT 0,
    UNIQUE(pool_id, member_id, nba_player_id)
);

CREATE INDEX idx_salary_rosters_pool ON salary_rosters(pool_id);
CREATE INDEX idx_salary_rosters_member ON salary_rosters(pool_id, member_id);

-- NBA Teams (reference data)
CREATE TABLE nba_teams (
    id INT PRIMARY KEY,
    name TEXT NOT NULL,
    abbreviation TEXT NOT NULL,
    conference TEXT NOT NULL CHECK (conference IN ('East', 'West')),
    seed INT,
    is_eliminated BOOLEAN NOT NULL DEFAULT FALSE,
    playoff_wins INT NOT NULL DEFAULT 0,
    playoff_losses INT NOT NULL DEFAULT 0
);

-- NBA Players (reference data)
CREATE TABLE nba_players (
    id INT PRIMARY KEY,
    name TEXT NOT NULL,
    team_id INT REFERENCES nba_teams(id),
    position TEXT NOT NULL,
    salary_value NUMERIC NOT NULL DEFAULT 0,
    playoff_points NUMERIC NOT NULL DEFAULT 0,
    playoff_rebounds NUMERIC NOT NULL DEFAULT 0,
    playoff_assists NUMERIC NOT NULL DEFAULT 0
);

CREATE INDEX idx_nba_players_team ON nba_players(team_id);

-- Game Results
CREATE TABLE game_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    espn_game_id TEXT UNIQUE NOT NULL,
    home_team_id INT NOT NULL REFERENCES nba_teams(id),
    away_team_id INT NOT NULL REFERENCES nba_teams(id),
    home_score INT NOT NULL,
    away_score INT NOT NULL,
    round INT NOT NULL CHECK (round BETWEEN 1 AND 4),
    series_id TEXT,
    game_date DATE NOT NULL
);

CREATE INDEX idx_game_results_date ON game_results(game_date);
CREATE INDEX idx_game_results_teams ON game_results(home_team_id, away_team_id);

-- Pool Standings (cached, updated after each game)
CREATE TABLE pool_standings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    member_id UUID NOT NULL REFERENCES pool_members(id),
    rank INT NOT NULL,
    total_points NUMERIC NOT NULL DEFAULT 0,
    points_breakdown JSONB NOT NULL DEFAULT '{}',
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(pool_id, member_id)
);

CREATE INDEX idx_pool_standings_pool ON pool_standings(pool_id);

-- RPC functions for atomic increments
CREATE OR REPLACE FUNCTION increment_wins(team_id INT)
RETURNS VOID AS $$
    UPDATE nba_teams SET playoff_wins = playoff_wins + 1 WHERE id = team_id;
$$ LANGUAGE SQL;

CREATE OR REPLACE FUNCTION increment_losses(team_id INT)
RETURNS VOID AS $$
    UPDATE nba_teams SET playoff_losses = playoff_losses + 1 WHERE id = team_id;
$$ LANGUAGE SQL;

-- Enable Supabase Realtime on tables that need it
ALTER PUBLICATION supabase_realtime ADD TABLE draft_picks;
ALTER PUBLICATION supabase_realtime ADD TABLE auction_bids;
ALTER PUBLICATION supabase_realtime ADD TABLE pool_standings;
