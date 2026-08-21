# NFL Survivor Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `survivor` pool type (HBK Dads' Survivor 2026 ruleset) to playoff-pools, live for NFL 2026 Week 1.

**Architecture:** A new pool type on the existing DB-driven competition registry. NFL 2026 is a `competitions` row; games ingest through the existing generic ESPN path; the weekly resolver reuses `services/scoring.py::match_outcomes`. Survivor has its own tables (`survivor_entries`/`survivor_picks`/`survivor_buybacks`), its own `routes/survivor.py`, and a pure `services/survivor.py` logic module. It does not touch draft/auction/standings machinery.

**Tech Stack:** Flask (app factory + blueprints), Supabase (Postgres via service client), Jinja server-rendered templates with optimistic-DOM JS, Vercel (`pdx1`), ESPN public API, The Odds API.

## Global Constraints

- Python 3.9 (repo `venv`); run tests with `source venv/bin/activate` first.
- Migrations are hand-applied in the Supabase SQL editor; the `.sql` file is the record. Next migration number is **010**.
- One entry per member. Money is offline — the app tracks status only (`fee` is display-only, no ledger).
- Tie counts as a WIN. Team can be used only once per season, even across buybacks (enforced by `UNIQUE(entry_id, team_ref)`).
- Pick lock = `min(kickoff_at of picked team's game, 1:00 PM ET Sunday of that week)`.
- All timestamps compared in ET (`zoneinfo.ZoneInfo("America/New_York")`), matching `services/espn_api.py::_ET`.
- Ruleset lives in `pool.survivor_config` (JSON), defaulting to HBK values (see spec).
- Commit after each task. Do not push/deploy unless asked. Work on branch `feat/nfl-survivor`.
- Spec: `docs/superpowers/specs/2026-08-21-nfl-survivor-design.md`.

## File Structure

- Create `migrations/010_survivor.sql` — schema (game_results additions + 3 survivor tables).
- Create `scripts/seed_nfl.py` — NFL 2026 competition + teams seed.
- Modify `services/espn_api.py` — populate `week` + `kickoff_at` for NFL results.
- Modify `services/sync.py` — persist `week` + `kickoff_at`.
- Create `services/survivor.py` — pure logic: pick-lock, resolver, mercy, buyback windows.
- Create `services/survivor_data.py` — Supabase data access (entries/picks/buybacks, batched board read, team-used-once).
- Create `routes/survivor.py` — blueprint: board view, pick submit, buyback, commissioner tools.
- Modify `routes/pools.py` + `app.py` — survivor creation + blueprint registration.
- Modify `services/odds.py` — NFL sport key + `spreads` market.
- Create `templates/pool/survivor_board.html`, `templates/pool/_survivor_pick.html`, `templates/pool/_survivor_commish.html`.
- Create `static/survivor.js` — optimistic pick save + stale-while-revalidate.
- Create tests: `tests/test_survivor_logic.py`, `tests/test_survivor_data.py`, `tests/test_survivor_routes.py`, plus additions to `tests/test_espn_api.py`, `tests/test_odds.py`.

---

## Phase 0 — Schema, seed, ingest

### Task 1: Migration 010 — schema

**Files:**
- Create: `migrations/010_survivor.sql`

**Interfaces:**
- Produces: `game_results.week INT`, `game_results.kickoff_at TIMESTAMPTZ`; tables `survivor_entries`, `survivor_picks`, `survivor_buybacks` with the constraints below.

- [ ] **Step 1: Write the migration**

```sql
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
```

- [ ] **Step 2: Apply it** in the Supabase SQL editor (paste the file, run). Confirm no errors.

- [ ] **Step 3: Commit**

```bash
git add migrations/010_survivor.sql
git commit -m "migrations(010): survivor tables + game_results week/kickoff_at"
```

### Task 2: Seed NFL 2026 competition + teams

**Files:**
- Create: `scripts/seed_nfl.py`

**Interfaces:**
- Produces: a `competitions` row (`league='nfl'`, `season=2026`, `espn_sport='football'`, `espn_slug='nfl'`, `event_filter={"season_type":2}`, `status='active'`) and 32 `teams` rows upserted on `(competition_id, ext_id)`.

- [ ] **Step 1: Write the seed script** (mirror `scripts/seed_world_cup.py`)

```python
# scripts/seed_nfl.py
"""Seed the NFL 2026 regular-season competition + 32 teams from ESPN.
Run once: python -m scripts.seed_nfl"""
import sys, requests
from services.supabase_client import get_service_client

ESPN_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"


def fetch_teams():
    r = requests.get(ESPN_TEAMS, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    out = []
    for t in r.json().get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        team = t["team"]
        out.append({
            "ext_id": int(team["id"]),
            "name": team["displayName"],
            "abbreviation": team["abbreviation"],
            "league": "nfl",
        })
    return out


def main():
    sb = get_service_client()
    existing = sb.table("competitions").select("id").eq("league", "nfl").eq("season", 2026).execute().data
    if existing:
        comp_id = existing[0]["id"]
        print(f"NFL 2026 already exists: {comp_id}")
    else:
        comp = sb.table("competitions").insert({
            "league": "nfl", "season": 2026, "name": "NFL 2026",
            "espn_sport": "football", "espn_slug": "nfl",
            "event_filter": {"season_type": 2}, "stages": [],
            "scoring_defaults": {"type": "survivor"}, "status": "active",
        }).execute().data[0]
        comp_id = comp["id"]
        print(f"Created competition {comp_id}")
    teams = fetch_teams()
    if len(teams) != 32:
        print(f"WARNING: expected 32 teams, got {len(teams)}", file=sys.stderr)
    for t in teams:
        sb.table("teams").upsert({"competition_id": comp_id, "grouping": None, "seed": None, **t},
                                 on_conflict="competition_id,ext_id").execute()
    print(f"Upserted {len(teams)} teams")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it** (needs live ESPN; may 403 if rate-limited — retry later): `python -m scripts.seed_nfl` → expect "Upserted 32 teams".

- [ ] **Step 3: Commit**

```bash
git add scripts/seed_nfl.py
git commit -m "feat(survivor): seed NFL 2026 competition + teams"
```

### Task 3: Ingest NFL week + kickoff_at

**Files:**
- Modify: `services/espn_api.py` (in `fetch_competition_results`, the `out.append({...})` dict, ~line 384)
- Modify: `services/sync.py` (the insert dict, ~line 40)
- Test: `tests/test_espn_api.py`

**Interfaces:**
- Consumes: existing `fetch_competition_results(competition, dates=None)`.
- Produces: each game dict gains `"week"` (int or None) and `"kickoff_at"` (ISO string or None); `sync_competition_results` writes both columns.

- [ ] **Step 1: Write the failing test** (append to `tests/test_espn_api.py`)

```python
def test_fetch_competition_results_extracts_week_and_kickoff(monkeypatch):
    from services import espn_api
    payload = {"events": [{
        "id": "401", "date": "2026-09-13T17:00Z",
        "week": {"number": 2}, "season": {"type": 2, "slug": ""},
        "competitions": [{
            "status": {"type": {"state": "post", "completed": True, "shortDetail": "Final"}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "abbreviation": "ATL", "displayName": "Atlanta Falcons"}, "score": "20", "winner": True},
                {"homeAway": "away", "team": {"id": "2", "abbreviation": "TB", "displayName": "Tampa Bay Buccaneers"}, "score": "10", "winner": False},
            ],
        }],
    }]}
    class R:
        def raise_for_status(self): pass
        def json(self): return payload
    monkeypatch.setattr(espn_api.requests, "get", lambda *a, **k: R())
    comp = {"espn_sport": "football", "espn_slug": "nfl", "league": "nfl", "event_filter": {"season_type": 2}}
    g = espn_api.fetch_competition_results(comp)[0]
    assert g["week"] == 2
    assert g["kickoff_at"] == "2026-09-13T17:00Z"
```

- [ ] **Step 2: Run to verify it fails**

Run: `source venv/bin/activate && python -m pytest tests/test_espn_api.py::test_fetch_competition_results_extracts_week_and_kickoff -v`
Expected: FAIL with `KeyError: 'week'`.

- [ ] **Step 3: Add the fields** in `services/espn_api.py`, inside the `out.append({...})` dict in `fetch_competition_results`:

```python
            "week": (ev.get("week") or {}).get("number"),
            "kickoff_at": ev.get("date"),
```

- [ ] **Step 4: Persist them** in `services/sync.py`, in the `sb.table("game_results").insert({...})` dict:

```python
            "week": game.get("week"),
            "kickoff_at": game.get("kickoff_at"),
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_espn_api.py -v`
Expected: PASS (the new test; pre-existing `test_fetch_scoreboard` may fail on live data — ignore).

- [ ] **Step 6: Commit**

```bash
git add services/espn_api.py services/sync.py tests/test_espn_api.py
git commit -m "feat(survivor): ingest NFL week + kickoff_at into game_results"
```

---

## Phase 1 — Core logic (pure, TDD) — `services/survivor.py`

### Task 4: Pick-lock computation

**Files:**
- Create: `services/survivor.py`
- Test: `tests/test_survivor_logic.py`

**Interfaces:**
- Produces: `pick_lock_at(kickoff_at: datetime, week_sunday: date, sunday_lock_et="13:00") -> datetime` and `is_locked(now, kickoff_at, week_sunday) -> bool`. All datetimes are ET-aware.

- [ ] **Step 1: Write the failing test** (`tests/test_survivor_logic.py`)

```python
from datetime import datetime, date
from zoneinfo import ZoneInfo
from services.survivor import pick_lock_at
ET = ZoneInfo("America/New_York")

def _et(y,m,d,h,mi): return datetime(y,m,d,h,mi,tzinfo=ET)

def test_thursday_pick_locks_at_kickoff():
    # TNF Thu 8:15 PM, week Sunday is the 13th -> lock at Thursday kickoff
    lock = pick_lock_at(_et(2026,9,10,20,15), date(2026,9,13))
    assert lock == _et(2026,9,10,20,15)

def test_sunday_late_pick_locks_at_1pm():
    # 4:25 PM Sunday game -> capped at 1 PM Sunday
    lock = pick_lock_at(_et(2026,9,13,16,25), date(2026,9,13))
    assert lock == _et(2026,9,13,13,0)

def test_monday_night_pick_locks_at_1pm_sunday():
    lock = pick_lock_at(_et(2026,9,14,20,15), date(2026,9,13))
    assert lock == _et(2026,9,13,13,0)

def test_saturday_pick_locks_at_kickoff():
    lock = pick_lock_at(_et(2026,12,20,13,0), date(2026,12,21))
    assert lock == _et(2026,12,20,13,0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_survivor_logic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.survivor'`.

- [ ] **Step 3: Implement**

```python
# services/survivor.py
"""Pure survivor logic: pick-lock, weekly resolution, mercy rule, buyback windows.
No network or DB — takes plain dicts/values, returns decisions. DB access lives
in services/survivor_data.py; ESPN/odds elsewhere."""
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _parse_lock_time(s):
    h, m = (int(x) for x in s.split(":"))
    return time(h, m)


def pick_lock_at(kickoff_at, week_sunday, sunday_lock_et="13:00"):
    """The instant a pick freezes: the earlier of the picked team's kickoff and
    1 PM ET on that week's Sunday. kickoff_at is an ET-aware datetime;
    week_sunday is a date."""
    anchor = datetime.combine(week_sunday, _parse_lock_time(sunday_lock_et), tzinfo=ET)
    return min(kickoff_at, anchor)


def is_locked(now, kickoff_at, week_sunday, sunday_lock_et="13:00"):
    return now >= pick_lock_at(kickoff_at, week_sunday, sunday_lock_et)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_survivor_logic.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/survivor.py tests/test_survivor_logic.py
git commit -m "feat(survivor): pick-lock computation min(kickoff, Sun 1PM ET)"
```

### Task 5: Weekly resolver

**Files:**
- Modify: `services/survivor.py`
- Test: `tests/test_survivor_logic.py`

**Interfaces:**
- Consumes: `services.scoring.match_outcomes(game)`.
- Produces: `resolve_week(entries, picks_by_entry, games_by_espn_id, week, mercy_after_week=7) -> {entry_id: {"result": "win"|"loss"|"tie"|"no_pick", "status": "active"|"eliminated", "eliminated_week": int|None}}`. `entries` is a list of `{"id","status","eliminated_week"}` (only active ones are graded). `picks_by_entry` maps entry_id → `{"week","team_ext_id","espn_game_id"}` or missing. `games_by_espn_id` maps espn_game_id → a game_results dict (has `home_team_id`,`away_team_id`,`home_score`,`away_score`,`is_draw`,`winner_team_id`).

- [ ] **Step 1: Write the failing tests**

```python
from services.survivor import resolve_week

def _game(gid, home, away, winner=None, draw=False):
    return {"espn_game_id": gid, "home_team_id": home, "away_team_id": away,
            "home_score": 1 if winner==home else 0, "away_score": 1 if winner==away else 0,
            "is_draw": draw, "winner_team_id": winner}

def test_resolver_win_survives_loss_eliminates():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None},
               {"id": "e2", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 1, "team_ext_id": 10, "espn_game_id": "g1"},
             "e2": {"week": 1, "team_ext_id": 20, "espn_game_id": "g1"}}
    games = {"g1": _game("g1", 10, 20, winner=10)}
    r = resolve_week(entries, picks, games, week=1)
    assert r["e1"]["status"] == "active" and r["e1"]["result"] == "win"
    assert r["e2"]["status"] == "eliminated" and r["e2"]["eliminated_week"] == 1

def test_resolver_tie_is_win():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 3, "team_ext_id": 10, "espn_game_id": "g1"}}
    games = {"g1": _game("g1", 10, 20, draw=True)}
    r = resolve_week(entries, picks, games, week=3)
    assert r["e1"]["result"] == "tie" and r["e1"]["status"] == "active"

def test_resolver_no_pick_eliminates():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None}]
    r = resolve_week(entries, {}, {}, week=2)
    assert r["e1"]["result"] == "no_pick" and r["e1"]["status"] == "eliminated"

def test_resolver_mercy_all_lose_after_week7_all_survive():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None},
               {"id": "e2", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 8, "team_ext_id": 10, "espn_game_id": "g1"},
             "e2": {"week": 8, "team_ext_id": 30, "espn_game_id": "g2"}}
    games = {"g1": _game("g1", 10, 20, winner=20), "g2": _game("g2", 30, 40, winner=40)}
    r = resolve_week(entries, picks, games, week=8)
    assert all(v["status"] == "active" for v in r.values())
    assert all(v["result"] == "loss" for v in r.values())  # graded loss, but not eliminated

def test_resolver_mercy_not_before_week7():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 5, "team_ext_id": 10, "espn_game_id": "g1"}}
    games = {"g1": _game("g1", 10, 20, winner=20)}
    r = resolve_week(entries, picks, games, week=5)
    assert r["e1"]["status"] == "eliminated"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_survivor_logic.py -k resolver -v`
Expected: FAIL — `resolve_week` not defined.

- [ ] **Step 3: Implement** (append to `services/survivor.py`)

```python
from services.scoring import match_outcomes


def _outcome_for(team_ext_id, game):
    """'win' | 'loss' | 'tie' for the given team in a resolved game."""
    for tid, outcome in match_outcomes(game):
        if tid == team_ext_id:
            return "tie" if outcome == "draw" else outcome
    return "loss"  # team not found in game -> treat as loss (defensive)


def resolve_week(entries, picks_by_entry, games_by_espn_id, week, mercy_after_week=7):
    """Grade one week. Only active entries are considered. Tie counts as a win
    (survive). A missing pick is a loss. If, after grading, no active entry
    survived AND week >= mercy_after_week, nobody is eliminated (mercy rule).
    Idempotent: depends only on inputs."""
    graded = {}
    survivors = 0
    for e in entries:
        if e["status"] != "active":
            continue
        pick = picks_by_entry.get(e["id"])
        if not pick:
            graded[e["id"]] = {"result": "no_pick", "survived": False}
            continue
        game = games_by_espn_id.get(pick["espn_game_id"])
        if game is None:
            # game not final yet -> leave pending, do not change status
            graded[e["id"]] = {"result": "pending", "survived": None}
            continue
        outcome = _outcome_for(pick["team_ext_id"], game)
        survived = outcome in ("win", "tie")
        graded[e["id"]] = {"result": outcome, "survived": survived}
        if survived:
            survivors += 1

    decided = [g for g in graded.values() if g["survived"] is not None]
    mercy = week >= mercy_after_week and survivors == 0 and len(decided) > 0

    out = {}
    for eid, g in graded.items():
        if g["survived"] is None:
            out[eid] = {"result": "pending", "status": "active", "eliminated_week": None}
        elif g["survived"] or mercy:
            out[eid] = {"result": g["result"], "status": "active", "eliminated_week": None}
        else:
            out[eid] = {"result": g["result"], "status": "eliminated", "eliminated_week": week}
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_survivor_logic.py -v`
Expected: PASS (all logic tests).

- [ ] **Step 5: Commit**

```bash
git add services/survivor.py tests/test_survivor_logic.py
git commit -m "feat(survivor): weekly resolver (match_outcomes, tie=win, mercy rule)"
```

### Task 6: Buyback windows

**Files:**
- Modify: `services/survivor.py`
- Test: `tests/test_survivor_logic.py`

**Interfaces:**
- Produces: `buyback_option(week, config) -> {"kind": "regular"|"super"|None, "fee": int|None, "limit": int|None}` describing what buyback (if any) is available to re-enter *for* the given upcoming week. `config` is `pool.survivor_config`.

- [ ] **Step 1: Write the failing tests**

```python
from services.survivor import buyback_option

CFG = {"regular_buyback": {"weeks": [1,6], "limit": None, "deadline": "sunday_1pm"},
       "super_buyback": {"weeks": [7,17], "limit": 1, "fee": 500, "deadline": "friday_2359_et"},
       "final_week": 18}

def test_regular_window_weeks_1_6():
    assert buyback_option(3, CFG)["kind"] == "regular"
    assert buyback_option(3, CFG)["limit"] is None

def test_super_window_weeks_7_17():
    o = buyback_option(9, CFG)
    assert o["kind"] == "super" and o["fee"] == 500 and o["limit"] == 1

def test_no_buyback_after_final_week():
    assert buyback_option(18, CFG)["kind"] is None
    assert buyback_option(19, CFG)["kind"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_survivor_logic.py -k buyback -v`
Expected: FAIL — `buyback_option` not defined.

- [ ] **Step 3: Implement** (append to `services/survivor.py`)

```python
def _in_window(week, win):
    lo, hi = win["weeks"]
    return lo <= week <= hi


def buyback_option(week, config):
    """What buyback is available to re-enter FOR `week`. None outside the
    windows or once past the final week."""
    reg = config.get("regular_buyback", {})
    sup = config.get("super_buyback", {})
    if reg and _in_window(week, reg):
        return {"kind": "regular", "fee": reg.get("fee"), "limit": reg.get("limit")}
    if sup and _in_window(week, sup):
        return {"kind": "super", "fee": sup.get("fee"), "limit": sup.get("limit")}
    return {"kind": None, "fee": None, "limit": None}
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_survivor_logic.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/survivor.py tests/test_survivor_logic.py
git commit -m "feat(survivor): buyback window resolution by week"
```

---

## Phase 2 — Data access + routes

### Task 7: Survivor data layer

**Files:**
- Create: `services/survivor_data.py`
- Test: `tests/test_survivor_data.py` (use a fake sb double; follow the fake-client pattern in `tests/test_sync.py`)

**Interfaces:**
- Produces:
  - `get_or_create_entry(sb, pool_id, member_id) -> entry dict`
  - `submit_pick(sb, entry, week, team_ref, espn_game_id, set_by="member", override_note=None) -> pick dict` — raises `TeamAlreadyUsed` if the team_ref is already on any of this entry's picks; upserts on `(entry_id, week)`.
  - `record_buyback(sb, entry, week, kind, fee=None)` — inserts a `survivor_buybacks` row and sets entry `status='active'`.
  - `board_data(sb, pool_id) -> {"entries": [...], "picks": {entry_id: {week: pick}}, "weeks": [...]}` — **three queries total**, assembled in memory (no N+1).
  - `apply_resolution(sb, resolution, week)` — writes `survivor_picks.result` and `survivor_entries.status/eliminated_week` from a `resolve_week` output.
- Defines: `class TeamAlreadyUsed(Exception)`.

- [ ] **Step 1: Write failing tests** for `submit_pick` raising `TeamAlreadyUsed` on reuse, and `board_data` issuing exactly three table reads. Use a fake `sb` recording `.table().select()/.insert()/.upsert()` calls (copy the double style from `tests/test_sync.py`).

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_survivor_data.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `services/survivor_data.py`.** Key rules: `submit_pick` first selects this entry's existing `team_ref`s (`survivor_picks` where `entry_id=…`) and raises `TeamAlreadyUsed` if the new `team_ref` is among them (belt-and-suspenders over the DB unique index); then upserts on `(entry_id, week)`. `board_data` runs exactly: (1) `survivor_entries` for the pool joined to member display names, (2) all `survivor_picks` for those entries, (3) `survivor_buybacks` for those entries — then assembles the nested dict in Python. Catch the Postgres unique-violation on insert and re-raise as `TeamAlreadyUsed` for the race case.

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add services/survivor_data.py tests/test_survivor_data.py
git commit -m "feat(survivor): supabase data layer (entries/picks/buybacks, batched board)"
```

### Task 8: Survivor routes — board + pick + buyback

**Files:**
- Create: `routes/survivor.py`
- Modify: `app.py` (register blueprint)
- Test: `tests/test_survivor_routes.py` (follow `tests/test_pools.py` client fixture)

**Interfaces:**
- Produces blueprint `survivor_bp` with:
  - `GET /pool/<pool_id>/survivor` → renders `survivor_board.html` via `board_data`.
  - `POST /pool/<pool_id>/survivor/pick` (JSON `{week, team_ref, espn_game_id}`) → enforces `is_locked` (reject 409 if locked), calls `submit_pick`, returns JSON `{ok, pick}`. Enables optimistic UI.
  - `POST /pool/<pool_id>/survivor/buyback` → validates `buyback_option(week, config)` is not None and super-limit not already used, calls `record_buyback`, returns JSON.
- Consumes: `services.survivor` (lock, buyback_option), `services.survivor_data`.

- [ ] **Step 1: Write failing tests** — a locked-week pick POST returns 409; an open-week pick POST returns 200 and persists; a buyback POST outside any window returns 400.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `routes/survivor.py`** following the `routes/auction.py` blueprint/`login_required`/`get_service_client` pattern. Compute `week_sunday` from the week's games (`min` game date that is a Sunday, else derive from any game's `kickoff_at`). Register in `app.py`:

```python
    from routes.survivor import survivor_bp
    app.register_blueprint(survivor_bp)
```

- [ ] **Step 4: Run to verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add routes/survivor.py app.py tests/test_survivor_routes.py
git commit -m "feat(survivor): board + pick(lock-enforced) + buyback routes"
```

### Task 9: Pool creation for survivor + commissioner tools

**Files:**
- Modify: `routes/pools.py` (creation dispatch ~line 209; add survivor branch + `survivor_config` default)
- Modify: `routes/survivor.py` (commissioner endpoints)
- Test: `tests/test_survivor_routes.py`

**Interfaces:**
- Produces: creating a pool with `type='survivor'` seeds `survivor_config` (HBK defaults from the spec) and creates a `survivor_entries` row for the creator; joining creates an entry. Commissioner endpoints (creator-only): `assign_pick` (bypasses lock, `set_by='commissioner'`, respects `TeamAlreadyUsed`), `record_buyback_for`, `set_status` (eliminate/reinstate), `resolve_now` (re-run resolver for a week), `settle_season`.

- [ ] **Step 1: Write failing tests** — survivor pool creation stores `survivor_config`; `assign_pick` succeeds on a locked week for the creator but 403 for a non-creator; `assign_pick` with an already-used team returns 400.

- [ ] **Step 2–4:** implement, run to green. Default config literal (copy from spec's `survivor_config`). Reuse the existing creator-check used in `routes/auction.py` commissioner actions.

- [ ] **Step 5: Commit**

```bash
git add routes/pools.py routes/survivor.py tests/test_survivor_routes.py
git commit -m "feat(survivor): pool creation + commissioner tools (assign/buyback/status/settle)"
```

---

## Phase 3 — Sync wiring + odds

### Task 10: Wire resolver into the sync path

**Files:**
- Modify: `routes/scores.py` (the throttled sync hook that calls `recalculate_standings`, ~line 167–182) and `api/cron/sync_games.py`
- Modify: `services/survivor_data.py` (add `resolve_and_apply(sb, pool)` convenience that loads entries/picks/games for the current NFL week and calls `resolve_week` + `apply_resolution`)
- Test: `tests/test_survivor_data.py`

**Interfaces:**
- Produces: after NFL games sync, each survivor pool's current-and-prior unresolved weeks are resolved. Idempotent — safe to run every poll/cron.

- [ ] **Step 1:** Write a test that `resolve_and_apply` on a week whose games are all final applies statuses, and is a no-op on a second call.
- [ ] **Step 2–4:** implement; hook it beside `recalculate_standings` for `type='survivor'` pools (mirror the existing "if new games synced, recompute" branch) and in the cron.
- [ ] **Step 5: Commit**

```bash
git add routes/scores.py api/cron/sync_games.py services/survivor_data.py tests/test_survivor_data.py
git commit -m "feat(survivor): resolve weeks on ESPN sync (cron + throttled poll)"
```

### Task 11: NFL odds — sport key + spreads

**Files:**
- Modify: `services/odds.py` (`_SPORT_KEY` ~line 32; add `spreads` to `markets` in `fetch_odds` ~line 87; add a spread parser)
- Test: `tests/test_odds.py`

**Interfaces:**
- Produces: `sport_key("nfl") == "americanfootball_nfl"`; `best_by_outcome` (or a new `best_spread_by_team`) returns the point spread per team from the `spreads` market.

- [ ] **Step 1: Write failing tests** — `sport_key("nfl")` resolves; a fixture odds event with a `spreads` market yields `{team: point}` with the favorite negative.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — add `"nfl": "americanfootball_nfl"` to `_SPORT_KEY`; change the `fetch_odds` params `"markets": "h2h"` → `"markets": "h2h,spreads"`; add `best_spread_by_team(event)` mirroring `best_by_outcome` but reading `market.key == "spreads"` and each outcome's `point`.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit**

```bash
git add services/odds.py tests/test_odds.py
git commit -m "feat(survivor): NFL odds sport key + spreads market"
```

---

## Phase 4 — UI (templates + optimism + SWR)

> Templates follow the existing `templates/pool/` patterns. Use `| tojson` on every value interpolated into `<script>`/`onclick` (repo rule — see `flask_inline_js_tojson`). Logos via `services.team_colors.team_logo_url("nfl", ext_id)`.

### Task 12: Status Board template (grid A)

**Files:**
- Create: `templates/pool/survivor_board.html`
- Modify: `routes/survivor.py` (board view passes `board_data` + current week)

**Interfaces:**
- Consumes: `board_data` shape from Task 7.

- [ ] **Step 1:** Render the members×weeks grid (layout A from the approved mockup `.superpowers/brainstorm/.../status-board.html`): header "X of N alive", per-cell pick abbreviation colored by `result` (win/tie green, loss red strike-through, no-pick grey), buyback ↩ marker, current week shown 🔒 (hidden) until lock, per-row `ALIVE` / `OUT · Wk N`, and a "commish" marker where `set_by='commissioner'`.
- [ ] **Step 2:** Manually verify by loading the page for a seeded test pool (use `/run` or local Flask): grid renders, alive count correct.
- [ ] **Step 3: Commit**

```bash
git add templates/pool/survivor_board.html routes/survivor.py
git commit -m "feat(survivor): status board grid template"
```

### Task 13: Weekly Pick screen template + optimistic save + SWR

**Files:**
- Create: `templates/pool/_survivor_pick.html`, `static/survivor.js`
- Modify: `routes/survivor.py` (pick view passes this week's games enriched with logos + spreads + used-team set)

**Interfaces:**
- Consumes: pick route from Task 8; odds `best_spread_by_team` from Task 11.

- [ ] **Step 1:** Render the pick screen from the approved mockup `pick-screen-v3.html`: team logos, point spread (favorite green), lock countdown, Thursday early-lock badge, used teams greyed with the week used, explicit "saved" state.
- [ ] **Step 2:** Implement `static/survivor.js`: on team click, optimistically mark selected + "unsaved"; POST to `/survivor/pick`; on 200 flip to "saved", on 409/error revert + toast. Stale-while-revalidate: cache board JSON in `localStorage` keyed `survivor:<pool>:<week>`, paint on load, then refresh from server and reconcile.
- [ ] **Step 3:** Manually verify: picking a team feels instant; a used team is unclickable; reload paints instantly then refreshes.
- [ ] **Step 4: Commit**

```bash
git add templates/pool/_survivor_pick.html static/survivor.js routes/survivor.py
git commit -m "feat(survivor): pick screen (logos+spreads) with optimistic save + SWR cache"
```

### Task 14: Commissioner panel template

**Files:**
- Create: `templates/pool/_survivor_commish.html`
- Modify: `templates/pool/survivor_board.html` (include the panel for the creator)

- [ ] **Step 1:** Render creator-only controls wired to the Task 9 endpoints: Assign Pick (member + team dropdown of that entry's unused teams), record/undo buyback, eliminate/reinstate toggle, "re-run resolver for week N", settle season.
- [ ] **Step 2:** Manually verify each control hits its endpoint and updates the board.
- [ ] **Step 3: Commit**

```bash
git add templates/pool/_survivor_commish.html templates/pool/survivor_board.html
git commit -m "feat(survivor): commissioner panel"
```

---

## Final verification

- [ ] Run the full suite: `source venv/bin/activate && python -m pytest -q` — all survivor tests green; only the two known pre-existing failures (`test_fetch_scoreboard`, `test_reject_over_salary_cap`) remain.
- [ ] Seed + smoke: create a survivor pool, join with a second user, submit picks, run the resolver against a completed week, confirm eliminations + buyback + status board.
- [ ] Update the vault doc `~/2nd brain/Projects/playoff-pools.md` (show-diff-first) with the shipped survivor feature.

## Open items (carry from spec)

- Super BuyBack deadline **confirmed: Friday 11:59 PM ET**.
- Post-Week-18 Playoff Draw settlement is commissioner-manual.
