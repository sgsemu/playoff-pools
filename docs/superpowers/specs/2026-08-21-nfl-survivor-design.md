# NFL Survivor Pool — Design Spec

**Date:** 2026-08-21
**Project:** playoff-pools
**Target:** live for NFL 2026 Week 1 (opener ~Sept 4, 2026)
**Status:** design — pending user review

## Overview

Add a **survivor** pool type to playoff-pools, modeled on the real
**"HBK Dads' Survivor 2026"** ruleset. Each week a member picks one NFL team to
win straight up; a loss eliminates them unless they buy back; a team can be used
only once all season. Built as a new pool type on the existing DB-driven
**competition registry** — NFL 2026 becomes a competition, and the weekly
resolver reuses the `match_outcomes` helper already in `services/scoring.py`.

The first consumer is the HBK Dads pool, currently run on Yahoo. Building it here
removes Yahoo's constraints (e.g., its 3-buyback cap) and automates the weekly
bookkeeping the commissioner does by hand.

## Ruleset (HBK Dads' Survivor 2026)

Source: commissioner's note (two screenshots) + follow-up clarifications.

1. **Pick one weekly winner, straight up (no spread).**
2. **A team can be used only once all season — even across buybacks.**
3. **A tie counts as a WIN** and advances.
4. **Lose → eliminated**, unless you buy back within the applicable window.
5. **Pick lock:** a member's weekly pick locks at
   `min(kickoff of the picked team's game, 1:00 PM ET Sunday of that week)`.
   This captures every case: a Thursday / Friday / Saturday / Thanksgiving pick
   locks at that game's kickoff; a Sunday-1pm, SNF, or MNF pick locks at 1 PM
   Sunday, so nobody waits on early outcomes.
6. **Regular buyback (Weeks 1–6):** unlimited. Must buy back before 1:00 PM
   Sunday of the weekend immediately after the loss.
7. **Super BuyBack (Weeks 7–17):** one per player for the whole window, **$500**,
   committed by **Friday** of that week. (Money is tracked as an informational
   note only — see Out of Scope.)
8. **Mercy rule (Week ≥ 7):** if everyone still alive loses the same weekend, all
   advance, no elimination, no tiebreaker — regardless of prior buybacks.
9. **After Week 18:** no buyback. If multiple players remain → commissioner
   settlement + Playoff Draw (handled manually).
10. **One entry per member.** Money (buy-in, buyback fees, payout) is handled
    offline; the app tracks status only.

## Approach

**Registry-integrated survivor pool type.** Reuse:

- `pools` / `pool_members` — creation, invite/join, membership.
- `competitions` — one new row: NFL 2026 regular season
  (`espn_sport='football'`, `espn_slug='nfl'`, `league='nfl'`,
  `event_filter={"season_type": 2}`).
- `teams` — NFL teams seeded from ESPN, addressed by `team_ref` (UUID) / `ext_id`.
- `game_results` — ingested by the existing generic `fetch_competition_results`
  / `sync_competition_results` path.
- `services/scoring.py::match_outcomes` — grades each pick (win/draw/loss).
- `services/odds.py` — sportsbook lines on the pick screen.
- ESPN sync hooks (daily cron + throttled standings-poll piggyback).

Survivor gets its **own** tables, routes (`routes/survivor.py`), and resolver, and
**does not** touch `draft_picks`, `auction_bids`, `salary_rosters`, or
`pool_standings`. None of that machinery applies.

## Data Model

### Additions to `game_results` (migration)

- `week INT NULL` — NFL week number from ESPN `event.week.number`. Numeric so
  buyback-window logic ("Week ≥ 7") is clean integer comparison.
- `kickoff_at TIMESTAMPTZ NULL` — real kickoff time from ESPN `event.date`,
  required for the pick-lock computation.

### New tables

```sql
survivor_entries               -- one row per member per survivor pool
  id             UUID PK
  pool_id        UUID → pools
  member_id      UUID → pool_members
  status         TEXT   -- 'active' | 'eliminated'
  eliminated_week INT NULL     -- week of most recent loss; NULL while active
  created_at     TIMESTAMPTZ
  UNIQUE(pool_id, member_id)

survivor_picks                 -- one row per entry per week
  id             UUID PK
  entry_id       UUID → survivor_entries
  week           INT
  team_ref       UUID → teams
  espn_game_id   TEXT          -- the picked team's game that week
  result         TEXT   -- 'pending' | 'win' | 'loss' | 'tie'
  set_by         TEXT   -- 'member' | 'commissioner'
  override_note  TEXT NULL     -- commissioner note when set_by='commissioner'
  created_at     TIMESTAMPTZ
  updated_at     TIMESTAMPTZ
  UNIQUE(entry_id, week)        -- one pick per week
  UNIQUE(entry_id, team_ref)    -- team used once per season (survives buybacks)

survivor_buybacks              -- one row per re-entry event
  id             UUID PK
  entry_id       UUID → survivor_entries
  week           INT           -- the week bought back in for
  kind           TEXT   -- 'regular' | 'super'
  fee            INT NULL       -- informational (e.g. 500 for super); no ledger
  committed_at   TIMESTAMPTZ
  -- partial unique: at most one super buyback per entry
  UNIQUE(entry_id) WHERE kind = 'super'
```

`UNIQUE(entry_id, team_ref)` is the load-bearing constraint: "one team per season,
even with a buyback" becomes impossible to violate at the database level.

### `pool.survivor_config` (JSON)

Ruleset as data, defaulting to HBK:

```json
{
  "tie_is_win": true,
  "sunday_lock_et": "13:00",
  "mercy_after_week": 7,
  "final_week": 18,
  "regular_buyback": { "weeks": [1, 6], "limit": null, "deadline": "sunday_1pm" },
  "super_buyback":   { "weeks": [7, 17], "limit": 1, "fee": 500, "deadline": "friday_2359_et" }
}
```

## Weekly Lifecycle & Pick Lock

State machine per pool, driven by the NFL calendar (no manual phase flip):

```
OPEN ──(picks lock individually)──▶ LOCKED ──(all week games final)──▶ RESOLVED ──▶ next week OPEN
```

- **OPEN** — members submit/change picks. Current week derived from
  `game_results.week` vs. today.
- **LOCKED** — each pick locks at `min(kickoff_at, Sunday 1PM ET anchor)`.
  Submission rejected when `now >= lock_instant`. After lock, picks reveal on the
  Status Board (hidden before lock).
- **RESOLVED** — once every game with that `week` is `is_complete`, the resolver
  grades picks.

The Sunday 1PM anchor for a week is derived from that week's games (the Sunday
date among them at 13:00 ET).

## Resolver

Pure function `resolve_survivor_week(pool, week)` over
`(survivor_picks, game_results)`:

For each active entry's pick, look up the picked team's `game_results` row for the
week and call `match_outcomes(game)`:

- `win` **or** `draw` → **survived** (tie = win falls out for free)
- `loss` → mark pick `result='loss'`, entry `status='eliminated'`,
  `eliminated_week = week`
- **no pick submitted** → treated as a loss (eliminated)

**Mercy rule:** after grading, if `week >= mercy_after_week` and the set of
survivors this week is **empty**, revert all eliminations for the week — everyone
advances. If ≥1 survives, normal elimination stands.

Properties: no network, fully unit-testable, and **idempotent** — re-running a
resolved week yields the same result (safe for the cron + self-healing inline
settle). Runs from the existing sync hook, like `recalculate_standings`.

## Buybacks

Explicit member action ("Buy Back" button on an eliminated entry when its window
is open): writes a `survivor_buybacks` row, flips `status` back to `active`, and
lets the member pick for the upcoming week. `UNIQUE(entry_id, team_ref)` still
blocks reusing a team. Windows (from `survivor_config`, by week number):

- **Weeks 1–6 — regular, unlimited.** Deadline 1:00 PM Sunday of the week after
  the loss.
- **Weeks 7–17 — super, one per entry, $500.** Deadline Friday 11:59 PM ET of that
  week. `fee=500` recorded as informational; reconciled offline.
- **After Week 18 — none.**

## Commissioner Tools

Pool creator = commissioner (reusing the existing draft/auction commissioner
pattern):

- **Assign Pick** — set/replace any member's pick for the current week,
  **bypassing the lock with full discretion** (any unused team, any time; for
  genuine tech-failure cases). Still respects `UNIQUE(entry_id, team_ref)`.
  Audited: `set_by='commissioner'` + `override_note`, shown as a "commish" marker
  on the Status Board cell.
- **Record / undo a buyback** on a member's behalf (BDV rule, offline disputes).
- **Eliminate / reinstate** an entry manually.
- **Re-run the resolver** for a week (idempotent) after a corrected game result.
- **Settle season** — mark winner(s), close the pool (Week 18+ / Playoff Draw
  outcome).

## Odds Integration

Pick screen enriches each game via the existing `services/odds.py`:

- Add `"nfl": "americanfootball_nfl"` to `_SPORT_KEY`.
- **Show the point spread** (not moneyline): add `spreads` to the odds `markets`
  param (currently `h2h`-only) and parse the point value; best line across books,
  favorite highlighted, book tag + referral link. 6-hour cache unchanged.
- **Team logos** via the existing `team_logo_url()` helper (ESPN logo CDN),
  replacing city abbreviations.

## UI Surfaces

1. **Status Board** — season grid (members × weeks), pool home view. Each cell:
   pick + outcome (survived / eliminated / no-pick / buyback marker); headline
   "X of N alive"; per-row ALIVE / OUT · Wk N. Current week hidden (🔒) until lock.
2. **Weekly Pick screen** — team logos, point spread, lock countdown, Thursday
   early-lock badge, used-teams greyed with the week used, explicit "saved"
   confirmation.
3. **Buy Back** — button on an eliminated entry within its window.
4. **Commissioner panel** — the tools above.
5. **Pool creation** — new `survivor` type, selects the NFL 2026 competition,
   seeds `survivor_config` defaults.

## Performance & Instant-Feel UX

Goal: survivor pages feel instant, especially the pick flow on mobile. Reuses the
app's existing performance playbook (June 2026 latency work) plus client optimism.

1. **Read precomputed state, never recompute on render.** The resolver writes
   `survivor_picks.result` and `survivor_entries.status` when games finish; the
   Status Board and pick screen only `SELECT`. No ESPN calls or scoring math on
   the render path.
2. **Batched reads (no N+1).** Status Board = one query for entries + one for the
   week's picks + one for the week's games, assembled in memory — O(1) in member
   count, mirroring the dashboard 13→6 query fix.
3. **Optimistic pick save.** Reuse the existing optimistic-DOM pattern (star-click
   optimism): the selected team highlights and flips to "saved" immediately
   client-side; the POST runs in the background and reconciles on response
   (revert + toast on failure). Snappy on mobile Safari.
4. **Stale-while-revalidate board.** Cache the last rendered board JSON in
   `localStorage`; on revisit, paint it instantly, then refresh from the server
   and reconcile. Keyed by pool + week so a resolved week doesn't show stale
   status for long.
5. **Cache external data.** Odds keep the 6-hour TTL cache; logos are static CDN
   URLs (browser-cached); ESPN results sync on the throttled cadence, off the hot
   path.
6. **Co-located compute.** Inherits the `pdx1`↔Supabase region pin (~3ms/query).

Not doing local-first/PWA (service worker + IndexedDB) — overkill for a friends
pool; the above gives instant-feel without a new stack.

## Test Plan

Extends the pytest suite; all logic is pure functions (no network):

- **Resolver:** win / tie / loss / no-pick → survive/eliminate; tie=win via
  `match_outcomes`; idempotent re-run.
- **Mercy rule:** survivors-empty after Wk 7 → all advance; one survivor → normal;
  before Wk 7 → no mercy.
- **Pick lock:** `min(kickoff, Sun 1PM ET)` across Thu / Sun-1pm / SNF / MNF / Sat;
  reject post-lock; commissioner Assign bypasses lock.
- **Team-used-once:** rejected on reuse, including across a buyback.
- **Buyback windows:** eligibility by week (1–6 unlimited; 7–17 super, one per
  entry, Friday deadline; 18+ none); status flip; team still unusable after.
- **NFL ingest:** `week` + `kickoff_at` populated; `football/nfl` flows through
  `fetch_competition_results`.
- **Odds:** `spreads` parse; `nfl` sport-key resolves.

## Out of Scope / Assumptions / Open Items

- **Money is not managed in-app.** Buy-in, buyback fees ($500 super), and payouts
  are offline; `fee` is display-only, no ledger.
- **Playoff Draw (post-Week-18 multi-survivor settlement)** is commissioner-manual;
  the app stops and shows remaining entries.
- **BDV rule** — offline commissioner discretion (covered by manual
  eliminate/reinstate).
- **Multiple entries per member** — not supported (one entry per member).
- **ASSUMPTION TO CONFIRM:** Super BuyBack deadline encoded as **Friday 11:59 PM
  ET**. The commissioner note says "by Friday"; the original screenshot said "by
  kickoff of the earliest game that week." These differ on weeks with a Thursday
  game. Using end-of-Friday ET unless corrected.

## Phasing note

Everything targets Week 1 per the user's call. Natural activation order if the
clock gets tight: Weeks-1–6 core (pick / lock / resolve / eliminate / regular
buyback / team-used-once) must ship before Sept 4; the super buyback (Wk 7),
mercy rule (Wk 7), and Week-18 settlement do not activate until mid-season and
can land shortly after launch without missing their windows.
