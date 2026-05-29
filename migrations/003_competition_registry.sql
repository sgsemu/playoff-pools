-- 003_competition_registry.sql
-- DB-driven competition registry. Additive + backward compatible:
-- existing nba_teams/nhl_teams and the legacy nba_team_id/team_id INT columns
-- are left in place and are dropped only in a later cleanup migration.

-- 1. Competitions: one row per drafted event (NBA playoffs, NHL playoffs, WC 2026)
CREATE TABLE IF NOT EXISTS competitions (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    league           TEXT NOT NULL,            -- 'nba' | 'nhl' | 'world_cup'
    season           INT  NOT NULL,
    name             TEXT NOT NULL,
    espn_sport       TEXT NOT NULL,            -- 'basketball' | 'hockey' | 'soccer'
    espn_slug        TEXT NOT NULL,            -- 'nba' | 'nhl' | 'fifa.world'
    event_filter     JSONB NOT NULL DEFAULT '{}',
    stages           JSONB NOT NULL DEFAULT '[]',
    scoring_defaults JSONB NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(league, season)
);

-- 2. Generic teams table (replaces nba_teams/nhl_teams as the draft source)
CREATE TABLE IF NOT EXISTS teams (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    competition_id UUID NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
    ext_id         INT  NOT NULL,              -- ESPN team id
    name           TEXT NOT NULL,
    abbreviation   TEXT NOT NULL,
    grouping       TEXT,                        -- conference (NBA/NHL) or group (WC)
    seed           INT,
    color          TEXT,
    eliminated     BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(competition_id, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_teams_competition ON teams(competition_id);

-- 3. Many-to-many: a pool draws from one or more competitions.
--    Single-league pools have one row; combined NBA+NHL pools have two.
CREATE TABLE IF NOT EXISTS pool_competitions (
    pool_id        UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    competition_id UUID NOT NULL REFERENCES competitions(id),
    PRIMARY KEY (pool_id, competition_id)
);
CREATE INDEX IF NOT EXISTS idx_pool_competitions_pool ON pool_competitions(pool_id);

-- 4. New UUID FK on picks/bids (named team_ref to avoid the legacy team_id INT)
ALTER TABLE draft_picks  ADD COLUMN IF NOT EXISTS team_ref UUID REFERENCES teams(id);
ALTER TABLE auction_bids ADD COLUMN IF NOT EXISTS team_ref UUID REFERENCES teams(id);

-- 5. Generalize game_results off nba_teams
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS competition_id UUID REFERENCES competitions(id);
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS stage TEXT;
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS is_draw BOOLEAN NOT NULL DEFAULT FALSE;

-- Drop the NBA-only foreign keys and the 1..4 round check so soccer/multi-league
-- results can be stored. Constraint names are Postgres defaults from the base schema.
ALTER TABLE game_results DROP CONSTRAINT IF EXISTS game_results_home_team_id_fkey;
ALTER TABLE game_results DROP CONSTRAINT IF EXISTS game_results_away_team_id_fkey;
ALTER TABLE game_results DROP CONSTRAINT IF EXISTS game_results_round_check;
ALTER TABLE game_results ALTER COLUMN round DROP NOT NULL;
