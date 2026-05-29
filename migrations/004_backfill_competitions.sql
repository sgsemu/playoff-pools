-- 004_backfill_competitions.sql
-- Backfill the registry from the legacy nba_teams/nhl_teams data, then wire
-- existing picks/bids/results/pools to it. Idempotent via ON CONFLICT / guards.

-- 1. NBA + NHL competition rows for the 2026 season
-- JSON values are dollar-quoted ($j$...$j$) with explicit ::jsonb casts so the
-- statement survives copy/paste into the Supabase SQL editor (inline
-- single-quoted JSON tripped its parser).
INSERT INTO competitions (league, season, name, espn_sport, espn_slug, event_filter, stages, scoring_defaults)
VALUES
 ('nba', 2026, 'NBA Playoffs 2026', 'basketball', 'nba',
  $j${"season_type":3}$j$::jsonb,
  $j$[{"key":"r1","label":"Round 1"},{"key":"r2","label":"Round 2"},{"key":"cf","label":"Conference Finals"},{"key":"finals","label":"Finals"}]$j$::jsonb,
  $j${}$j$::jsonb),
 ('nhl', 2026, 'NHL Playoffs 2026', 'hockey', 'nhl',
  $j${"season_type":3}$j$::jsonb,
  $j$[{"key":"r1","label":"Round 1"},{"key":"r2","label":"Round 2"},{"key":"cf","label":"Conference Finals"},{"key":"finals","label":"Stanley Cup Final"}]$j$::jsonb,
  $j${}$j$::jsonb)
ON CONFLICT (league, season) DO NOTHING;

-- 2. Copy legacy team tables into the generic teams table
INSERT INTO teams (competition_id, ext_id, name, abbreviation, grouping, seed, eliminated)
SELECT c.id, n.id, n.name, n.abbreviation, n.conference, n.seed, n.is_eliminated
FROM nba_teams n CROSS JOIN competitions c
WHERE c.league = 'nba' AND c.season = 2026
ON CONFLICT (competition_id, ext_id) DO NOTHING;

INSERT INTO teams (competition_id, ext_id, name, abbreviation, grouping, seed, eliminated)
SELECT c.id, h.id, h.name, h.abbreviation, h.conference, h.seed, h.is_eliminated
FROM nhl_teams h CROSS JOIN competitions c
WHERE c.league = 'nhl' AND c.season = 2026
ON CONFLICT (competition_id, ext_id) DO NOTHING;

-- 3. Point existing picks/bids at the new team rows.
--    Legacy league is on the row; legacy team id is COALESCE(team_id, nba_team_id).
UPDATE draft_picks p SET team_ref = t.id
FROM teams t JOIN competitions c ON t.competition_id = c.id
WHERE c.league = p.league AND c.season = 2026
  AND t.ext_id = COALESCE(p.team_id, p.nba_team_id)
  AND p.team_ref IS NULL;

UPDATE auction_bids b SET team_ref = t.id
FROM teams t JOIN competitions c ON t.competition_id = c.id
WHERE c.league = b.league AND c.season = 2026
  AND t.ext_id = COALESCE(b.team_id, b.nba_team_id)
  AND b.team_ref IS NULL;

-- 4. Stamp existing game_results with a competition_id by league.
UPDATE game_results g SET competition_id = c.id
FROM competitions c
WHERE c.league = COALESCE(g.league, 'nba') AND c.season = 2026
  AND g.competition_id IS NULL;

-- 5. Wire every existing pool to BOTH competitions (preserves combined NBA+NHL drafts).
INSERT INTO pool_competitions (pool_id, competition_id)
SELECT p.id, c.id
FROM pools p CROSS JOIN competitions c
WHERE c.season = 2026 AND c.league IN ('nba','nhl')
ON CONFLICT (pool_id, competition_id) DO NOTHING;

-- 6. Now that team_ref is populated, enforce one team per pool (kills the
--    double-pick race). Partial unique index ignores not-yet-migrated NULLs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_picks_pool_team
    ON draft_picks(pool_id, team_ref) WHERE team_ref IS NOT NULL;
