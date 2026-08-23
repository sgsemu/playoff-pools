-- 014_odds_cache.sql
-- Persistent (cross-process) cache for The Odds API / OddsPapi payloads.
-- Replaces services/odds.py's in-process _CACHE dict, which protects
-- nothing on Vercel serverless (every cold render is a fresh process).
-- Reads are cache-ONLY; only a scheduled refresh (cron) is allowed to call
-- the upstream APIs and write rows here.
CREATE TABLE IF NOT EXISTS odds_cache (
    cache_key         TEXT PRIMARY KEY,          -- e.g. 'oddsapi:americanfootball_nfl', 'oddsapi:_meta'
    payload           JSONB,
    fetched_at        TIMESTAMPTZ,
    refreshing_until  TIMESTAMPTZ
);
