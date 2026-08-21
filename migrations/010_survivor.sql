-- 010_survivor.sql
-- Survivor pool type (HBK Dads' Survivor 2026). Additive.

-- NFL games need a week number + real kickoff for pick-lock logic.
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS week INT;
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS kickoff_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS survivor_entries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pool_id         UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
    member_id       UUID NOT NULL REFERENCES pool_members(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'eliminated'
    eliminated_week INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(pool_id, member_id)
);

CREATE TABLE IF NOT EXISTS survivor_picks (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_id      UUID NOT NULL REFERENCES survivor_entries(id) ON DELETE CASCADE,
    week          INT  NOT NULL,
    team_ref      UUID NOT NULL REFERENCES teams(id),
    espn_game_id  TEXT,
    result        TEXT NOT NULL DEFAULT 'pending',    -- pending|win|loss|tie
    set_by        TEXT NOT NULL DEFAULT 'member',     -- member|commissioner
    override_note TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(entry_id, week),
    UNIQUE(entry_id, team_ref)
);

CREATE TABLE IF NOT EXISTS survivor_buybacks (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entry_id     UUID NOT NULL REFERENCES survivor_entries(id) ON DELETE CASCADE,
    week         INT  NOT NULL,
    kind         TEXT NOT NULL,                        -- 'regular' | 'super'
    fee          INT,
    committed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one super buyback per entry (whole 7-17 window).
CREATE UNIQUE INDEX IF NOT EXISTS uniq_super_buyback
    ON survivor_buybacks(entry_id) WHERE kind = 'super';

CREATE INDEX IF NOT EXISTS idx_survivor_entries_pool ON survivor_entries(pool_id);
CREATE INDEX IF NOT EXISTS idx_survivor_picks_entry_week ON survivor_picks(entry_id, week);
CREATE INDEX IF NOT EXISTS idx_game_results_week ON game_results(competition_id, week);
