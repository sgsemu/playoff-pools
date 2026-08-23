# Odds cache — shared Supabase cache + cron warm-up + self-healing inline refresh

- **Status:** design approved through Sections 1–2; Section 3 pending final read-through
- **Date:** 2026-06-27
- **Author:** Sean (with Claude)

## Update 2026-08-23 — shipped as cache-only + cron-cadence + governor

Section 2's self-healing inline refresh (atomic `refreshing_until` claim on a
stale read) was **not** shipped. Owner's call for strict free-tier control:
reads (`fetch_odds`, `_fetch_oddspapi_caesars`, `_oddspapi_participants`) are
**cache-only** and never call an upstream API under any condition, including a
stale/cold row. The **only** writer is `refresh_odds_lines(league)` (+ the
OddsPapi `refresh_*` counterparts), invoked exclusively by the daily
`sync-games` cron (Stage 2a, `api/cron/sync_games.py`) — the one Hobby-tier
cron slot available for this — gated by a credit-remaining governor
(`can_refresh(floor=60)`) sourced from The Odds API's
`x-requests-remaining`/`x-requests-used` response headers, recorded on both
success and error responses. `refreshing_until` and the concurrency-claim
dance described below are unused. A props refresh (Thu–Sun, more frequent
than daily) is a follow-up, not yet built.

## Problem

The sportsbook-odds feature (`services/odds.py`, `services/bookmakers.py`) stopped
working: both upstream APIs are **out of quota**, not broken.

- **The Odds API** — free tier 500/month exhausted (`x-requests-remaining: 0`,
  `used: 500`), returns `401 OUT_OF_USAGE_CREDITS`. The `soccer_fifa_world_cup`
  sport key is valid and in-season, so matching/parsing logic is fine.
- **OddsPapi** (Caesars supplement) — `429 REQUEST_LIMIT_EXCEEDED`, 250-request
  cap exceeded.

### Root cause

The caches are **in-process** (`_CACHE`, `_OP_*_CACHE` module globals in
`odds.py`). The docstring budget math ("~4 sport keys once per 6h → well inside
500/month") assumes one long-lived process. On **Vercel serverless** every cold
start is a fresh process with an empty cache, so the 6h TTL protects almost
nothing — each cold render of the calendar re-hits the API from zero.
`enrich_calendar_with_best_odds` is on hot paths (dashboard `pools.py:275`,
scores page + auto-refresh partial `scores.py:47`/`:91`, detail `scores.py:124`),
and Vercel spins many concurrent instances under polling load — each cold one a
real credit. That is how 500 evaporated. (The ESPN reads share the same
ineffective-cache pattern but ESPN is free, so it never bit us.)

## Constraints

- **Vercel Hobby:** max 2 cron jobs, once-daily each, **both slots already used**
  (`sync-games` 0 6 * * *, `settle-auctions` 0 21 * * *). No new dedicated odds
  cron and no sub-daily schedule available.
- Friends pool, free tiers only. No plan upgrade in scope.

## Decision

Option 2: **shared Supabase cache + cron warm-up + self-healing inline refresh.**
Freshness target: cron + self-healing inline (intraday), chosen over once-daily
and inline-only.

---

## Section 1 — Cache layer (data + boundary) — APPROVED

New Supabase table `odds_cache`, one row per upstream payload:

| column | type | notes |
|---|---|---|
| `cache_key` | text, PK | e.g. `oddsapi:soccer_fifa_world_cup`, `oddspapi:world_cup`, `oddspapi_participants:10` |
| `payload` | jsonb | raw upstream response, stored verbatim |
| `fetched_at` | timestamptz | when this payload was last successfully written |
| `refreshing_until` | timestamptz, null | concurrency claim (Section 2) |

Cache sits at the **fetch boundary only** — `fetch_odds()`,
`_fetch_oddspapi_caesars()`, `_oddspapi_participants()`. Everything downstream
(`best_by_outcome`, `_maybe_promote_caesars`, name aliasing, orientation-swap)
is untouched because it still operates on the same raw payload shapes. We change
*where the bytes come from*, not what they mean.

The in-process `_CACHE` / `_OP_*_CACHE` dicts are **replaced** by Supabase reads
(optionally a tiny per-request memo so one render doesn't re-query Supabase for
the same key — not the source of truth). TTL stays **6h**.

## Section 2 — Refresh logic (read path, self-heal, concurrency) — APPROVED

Every fetch function follows:

```
read row for cache_key from odds_cache
if row is fresh (now - fetched_at < 6h):
    return row.payload          # common case — pure DB read, no API call
else:
    try to CLAIM the refresh (atomic):
        UPDATE odds_cache
        SET refreshing_until = now() + 90s
        WHERE cache_key = ?
          AND (refreshing_until IS NULL OR refreshing_until < now())
    if we won the claim (1 row updated):
        call the upstream API
        on success: write payload + fetched_at = now(), clear refreshing_until
        on failure: clear the claim, return stale payload (or [] if none)
    else (someone else is refreshing):
        return the stale payload immediately   # never block on the API
```

- The **atomic conditional UPDATE is the concurrency guard** — only one instance
  wins the claim per TTL window, so a herd of cold starts produces exactly one
  upstream call. Others serve slightly-stale data rather than waiting.
- The 90s claim auto-expires so a crashed/timed-out refresh can't wedge the cache.
- **Quota-exhaustion behavior** (today's situation): claim winner calls API, gets
  `401`/`429`, fails gracefully, returns stale-or-empty, clears claim. Worst case
  across a fully-exhausted month is ~4 failed probe calls/day total. When quota
  resets, the next claim succeeds and odds light up automatically.
- **Cron warm-up:** the existing `sync-games` cron (6am, `api/cron/sync_games.py`)
  gains a step that calls each in-season league's fetch once, pre-warming the
  cache. Reuses the same claim-aware fetch functions — no separate code path.

## Section 3 — Request paths, degradation, testing — PENDING FINAL READ

- **Request paths become strictly read-only against the API.**
  `enrich_calendar_with_best_odds`, `get_event_for_game`,
  `caesars_bookmaker_for_event` keep exact signatures; internally they hit
  Supabase via the claim-aware fetch, never a synchronous outbound call on the
  hot path (except the rare claim-winner). Side win: removes the up-to-8s × 2-API
  blocking call from cold page renders.
- **Degradation when cache is empty/cold:** unchanged contract. Fetch returns
  `[]`, no event matches, `best_odds` key never set; templates already guard with
  `{% if g.best_odds %}` and the detail page with `{% if odds_event %}`. Empty
  cache = no chips, never an error.
- **Testing:**
  - Unit-test claim logic with a faked Supabase client: fresh row → no API call;
    stale + won claim → API called once + written back; stale + lost claim →
    stale returned, zero API calls; API failure → stale/empty returned + claim
    cleared.
  - Existing odds tests (`best_by_outcome`, aliasing, orientation swap) stay green
    untouched — payload shapes unchanged.
  - One migration under `migrations/` for `odds_cache`, applied to live Supabase
    (consistent with the registry migrations).

## Out of scope (deliberate)

No new bookmakers, no plan upgrade, no change to odds-matching/aliasing logic,
no backfill of the dead NBA/NHL OddsPapi tournament ids.

## Open follow-ups (not blockers)

- Verify The Odds API monthly **reset date** (billing anniversary, not the 1st)
  so we know when odds return.
- The odds feature is **absent from the vault project doc**
  (`~/2nd brain/Projects/playoff-pools.md` mentions only ESPN) — ~7 shipped
  commits with no runbook coverage. Add a surface entry as part of this work.
