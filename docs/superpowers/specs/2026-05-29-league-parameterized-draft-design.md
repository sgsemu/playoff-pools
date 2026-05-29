# League-Parameterized Draft Engine + 2026 World Cup

## Problem

The app's draft, scoring, and live-data layers are hardcoded to NBA + NHL:
separate `nba_teams`/`nhl_teams` tables, a legacy `nba_team_id` column on
picks, `pools.league` defaulting to `'nba'`, a `game_results.home_team_id`
foreign key to `nba_teams`, and a `round BETWEEN 1 AND 4` check. There is no
way to run a pool that drafts from a different competition.

We want to run a **2026 World Cup** national-team draft on the same framework,
and to make adding future competitions (e.g. an NFL survivor pool) a
data-entry task rather than a fork. The World Cup kicks off ~June 11, 2026, so
the work is time-boxed.

## Goals

- A **DB-driven competition registry** so a pool draws its draftable teams,
  stage labels, and scoring defaults from data, not hardcoded branches.
- Ship a **2026 World Cup national-team draft** (48 teams) with **stage-weighted
  scoring**: per-win points that vary by stage, plus group draws and a
  group-winner bonus.
- **Existing combined NBA + NHL playoff pools keep working** (required), since
  those playoffs are mid-flight.
- Fix the confirmed draft bugs (wrong round/stage labels; members joining
  mid-draft) and the two cheap adjacent ones (double-pick race; leftover
  undraftable teams).

## Non-goals

- NFL survivor mode (future — a new competition *mode*, separate spec).
- Player-level / hybrid drafts. National teams only.
- Reworking auction or salary-cap formats beyond what the registry refactor
  forces.
- Redesigning the scores/standings UI beyond making it competition-aware.

## Decisions already made (from brainstorming)

| Question | Decision |
|---|---|
| Draftable unit (WC) | National teams |
| Code strategy | Generalize into a league-parameterized engine |
| Data model | DB-driven competition registry |
| WC scoring | Stage-weighted per-win + group draws + group-winner bonus (numbers below) |
| Bugs to fix | Round labels + join-mid-draft (confirmed) **plus** double-pick + leftover-teams (adjacent) |
| Combined NBA+NHL pools | Must keep working → many-to-many `pool_competitions` |

## Data model

### New tables

```sql
competitions (
  id            UUID PK,
  league        TEXT NOT NULL,        -- 'nba' | 'nhl' | 'world_cup'
  season        INT  NOT NULL,        -- e.g. 2026
  name          TEXT NOT NULL,        -- 'NBA Playoffs 2026', 'FIFA World Cup 2026'
  espn_sport    TEXT NOT NULL,        -- 'basketball' | 'hockey' | 'soccer'
  espn_slug     TEXT NOT NULL,        -- 'nba' | 'nhl' | 'fifa.world'
  event_filter  JSONB NOT NULL,       -- how to select relevant ESPN events (see below)
  stages        JSONB NOT NULL,       -- ordered [{key,label,advancement_bonus}]
  scoring_defaults JSONB NOT NULL,    -- seeds the pool's combo config
  status        TEXT NOT NULL DEFAULT 'active',
  UNIQUE(league, season)
)

teams (
  id            UUID PK,
  competition_id UUID NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  ext_id        INT  NOT NULL,        -- ESPN team id
  name          TEXT NOT NULL,
  abbreviation  TEXT NOT NULL,
  grouping      TEXT,                 -- conference (NBA/NHL) or group (WC)
  seed          INT,
  color         TEXT,                 -- primary color, nullable
  eliminated    BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE(competition_id, ext_id)
)

pool_competitions (
  pool_id        UUID NOT NULL REFERENCES pools(id) ON DELETE CASCADE,
  competition_id UUID NOT NULL REFERENCES competitions(id),
  PRIMARY KEY (pool_id, competition_id)
)
```

A single-competition pool (the common case, incl. World Cup) has one
`pool_competitions` row. A combined NBA+NHL pool has two.

### `competitions.stages` shape

Ordered array (order drives display labels). Points are awarded **per win at
the stage** — `win_points` — rather than a flat advancement bonus. The group
stage additionally scores draws and a placement bonus for finishing 1st.

```jsonc
// world_cup 2026 — per-WIN points by stage; group also scores draws + 1st-place bonus
[ {"key":"group","label":"Group Stage","win_points":3,"draw_points":1,"group_winner_bonus":2},
  {"key":"r32","label":"Round of 32","win_points":3},
  {"key":"r16","label":"Round of 16","win_points":3},
  {"key":"qf","label":"Quarterfinal","win_points":3},
  {"key":"sf","label":"Semifinal","win_points":4},
  {"key":"final","label":"Final (winner = champion)","win_points":5},
  {"key":"third_place","label":"Third-Place Playoff","win_points":3} ]
```

Winning the Final (5 pts) *is* the champion award — no separate champion bonus.
NBA/NHL competitions get a 4-stage array (R1, R2, Conf Finals, Finals)
mirroring today's behavior; their existing scoring config is unchanged.

### `competitions.event_filter`

Encodes how `espn_api` selects relevant events for the competition, replacing
the hardcoded `season.type == 3` playoff filter:
- NBA/NHL: `{"season_type": 3}`.
- World Cup: `{"all_tournament": true}` (every `fifa.world` fixture in the
  season counts).

### Changes to existing tables

- `draft_picks` / `auction_bids`: add `team_id UUID REFERENCES teams(id)` as
  the source of truth (the team row carries its competition + league). Keep
  the legacy `nba_team_id`/`team_id INT`/`league` columns through the
  transition; drop them in a later cleanup phase.
- **Add `UNIQUE(pool_id, team_id)` to `draft_picks`** → kills the double-pick
  race at the DB level.
- `game_results`:
  - Add `competition_id UUID REFERENCES competitions(id)`.
  - Add `stage TEXT` (the `stages[].key` the match belongs to).
  - Add `is_draw BOOLEAN NOT NULL DEFAULT FALSE`.
  - **Drop** the `home_team_id → nba_teams` FK and the `round BETWEEN 1 AND 4`
    check (use `home_team_id`/`away_team_id` as ESPN ints scoped by
    `competition_id`; `round` becomes legacy/nullable).
- `pools`: add `competition_id` is **not** added — membership lives in
  `pool_competitions`. `pools.league` becomes legacy (kept, ignored).

## Migration & backward compatibility

Backward-compatible, phased, no destructive drops until code has switched
over:

1. Create `competitions`, `teams`, `pool_competitions`; add new columns +
   the unique constraint.
2. Backfill: insert `nba/2026` and `nhl/2026` competition rows; copy
   `nba_teams` → `teams` (competition=nba, `ext_id` = old INT id) and
   `nhl_teams` → `teams` (competition=nhl). Build an `(league, ext_id) →
   teams.id` map and populate `draft_picks.team_id`, `auction_bids.team_id`,
   `game_results.competition_id`.
3. For every existing pool, insert `pool_competitions` rows for **both** the
   nba and nhl competitions → preserves today's combined-draft behavior.
4. Seed the World Cup 2026 competition + 48 teams (Phase 2).
5. Only after code reads the new tables in prod: a later cleanup migration
   drops `nba_team_id`/legacy `team_id INT`/`league`/`pools.league` and the
   `nba_teams`/`nhl_teams` tables.

## ESPN integration (`services/espn_api.py`)

Parameterize every fetch by a competition:
- Base URL = `site.api.espn.com/.../sports/{espn_sport}/{espn_slug}`.
- Event selection driven by `event_filter` (not the hardcoded `type==3`).
- **Stage resolution** — `resolve_stage(event, competition) -> stage_key`.
  For NBA/NHL, map ESPN's round → the 4-stage array. For the World Cup, map
  ESPN's fixture round indicator → the WC stage keys. The exact ESPN field for
  WC stage is confirmed in **Phase 0** by probing the live `fifa.world`
  scoreboard; the model stores a resolved `stage` string regardless of source,
  with a date-window fallback if the field is unreliable.
- **Draw detection** — prefer ESPN's `competitor.winner` boolean; a match with
  no winner and equal score is a draw (`is_draw = true`). Knockout matches
  always resolve a winner (ET/penalties via the `winner` flag).
- Keep the calendar/live-games functions but iterate the competitions a pool
  references instead of the fixed `[(NBA, nhl)]` pair.

## Draft engine (`routes/draft.py`)

- `_get_all_teams(sb, pool)` reads `teams WHERE competition_id IN (pool's
  competitions)` instead of `nba_teams`/`nhl_teams`. Picks reference
  `teams.id`.
- Snake logic (`_get_snake_order`, ordering) unchanged.
- **Fix leftover teams** — round count uses `ceil(total_teams / members)` so
  every team is draftable (last round may be partial; guard picks past the
  real team count).
- **Fix round labels** — both live `make_pick` and creator `assign_pick`
  derive the draft round from the *same* source (the snake sequence index),
  removing the divergent `((pick_order-1)//num_members)+1` formula. Note: the
  draft "round" (pick round) is distinct from a competition *stage*; keep the
  two concepts separate and named clearly.

## Scoring (`services/scoring.py`, `routes/scores.py`)

A new, competition-scoped scoring path computes points **per match result,
weighted by stage** (driven by the competition's `stages` win_points):

```
for each team a member holds:
  for each completed match the team played:
    if match.stage == 'group':
        if win:    points += stages['group'].win_points    # 3
        elif draw: points += stages['group'].draw_points    # 1
    else:  # knockout, incl. third_place
        if win:    points += stages[match.stage].win_points # r32/r16/qf=3, sf=4, final=5, third=3
  if team finished 1st in its group:
        points += stages['group'].group_winner_bonus        # 2
```

`recalculate_standings`:
- Award per-match points by `game_results.stage`, using `is_draw` so a tie
  never wrongly credits the away team (today's `home>away else away` logic is
  wrong for draws). Knockout matches always resolve a winner.
- Add the group-winner bonus to each team finishing 1st in its group —
  determined from ESPN group standings (rank 1), with a results-based FIFA
  tiebreaker computation as fallback.
- Key everything by `(competition_id, ext_id)` (or `teams.id`) so NBA/NHL
  id collisions stay separated, as today.
- This is a **new, competition-scoped** path; the existing NBA/NHL
  `per_win`/`per_round`/`combo` code stays untouched (its `series_wins = {}`
  TODO is out of scope for this work).

`_sync_completed_games`:
- Iterate the competitions present across active pools, not the fixed NBA+NHL
  pair.
- Write the **resolved `stage`** (not the hardcoded `round: 1`) and `is_draw`.

## Other code touchpoints

- `routes/pools.py` `join_pool`: **reject join when `draft_status != 'pending'`**
  (confirmed bug). Return a clear flash; do not insert the member.
- `routes/pools.py` `create_pool`: write `pool_competitions` rows from the form
  instead of the hardcoded `league: 'nba'`.
- `services/team_colors.py`: source colors from `teams.color`, with a
  deterministic fallback for competitions without curated colors (WC).

## UI

- **Create pool** (`templates/pool/create.html`): add a competition picker —
  presets for "World Cup 2026", "NBA + NHL Playoffs" (the combined default),
  "NBA Playoffs", "NHL Playoffs". Multi-select allowed but single is default.
- **Draft room / scores**: replace East/West conference assumptions with the
  generic `teams.grouping` (group for WC). Stage labels come from
  `competitions.stages`.
- No visual redesign beyond competition-awareness.

## Testing

- Keep the existing NBA/NHL suite green (regression guard for combined pools).
- New: competition registry + `pool_competitions` resolution; WC combo scoring
  incl. draws and stage-advancement and champion bonus; `join_pool` guard
  rejects mid-draft joins; unique-constraint blocks a double pick; `ceil`
  round math leaves no team undraftable; `resolve_stage` mapping for WC.
- A `fixtures`/recorded-ESPN-response approach for `espn_api` soccer parsing
  (don't hit the live API in tests).

## Phasing (for the implementation plan)

- **Phase 0 — De-risk**: probe the live `fifa.world` ESPN endpoint; confirm
  fixtures exist, the team-id set, and the stage field. (Blocks the rest.)
- **Phase 1 — Schema**: registry tables, `pool_competitions`, new columns,
  unique constraint, drop nba FK + round check; backfill preserving combined
  pools.
- **Phase 2 — Seed**: WC 2026 competition + 48 teams; backfilled NBA/NHL.
- **Phase 3 — ESPN**: competition-aware fetch + `resolve_stage` + draw
  detection; sync writes stage/draw.
- **Phase 4 — Engine + scoring**: draft reads by competition; combo scoring
  with draws + stage advancement; `recalculate_standings` rewrite.
- **Phase 5 — Bug fixes**: round-label unification, join guard, ceil math
  (double-pick handled by Phase 1's constraint).
- **Phase 6 — UI**: create-pool competition picker; competition-aware draft +
  scores templates; team-color fallback.
- **Phase 7 — Tests**.

**MVP slice for the deadline**: a single-competition World Cup pool that drafts
48 teams, runs a snake draft, and scores combo from live results — i.e.
Phases 0–5 plus the minimal create-pool WC option. Combined NBA+NHL
preservation is **not** deferrable (the migration must not break them). NFL
survivor is deferred.

## Scoring numbers (confirmed)

- **Group:** win 3, draw 1, **+2 bonus for finishing 1st in group**.
- **Knockout wins:** R32 / R16 / QF = 3 each; **SF = 4**; **Final = 5** (the
  Final winner is the champion — no separate champion bonus).
- **Third-place playoff win = 3**.
