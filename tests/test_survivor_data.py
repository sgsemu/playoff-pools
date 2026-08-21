import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest
from postgrest.exceptions import APIError

from services.survivor_data import (
    TeamAlreadyUsed,
    get_or_create_entry,
    submit_pick,
    record_buyback,
    board_data,
    apply_resolution,
    resolve_and_apply,
)


# ---------------------------------------------------------------------------
# Fake Supabase client double, following the recording-double style used in
# tests/test_sync.py -- but generalized across several tables/verbs since
# this module drives entries, picks, and buybacks. No network, no real DB.
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, sb, table, verb, payload=None, on_conflict=None):
        self.sb = sb
        self.table = table
        self.verb = verb
        self.payload = payload
        self.on_conflict = on_conflict
        self.filters = []

    def eq(self, col, val):
        self.filters.append((col, "eq", val))
        return self

    def in_(self, col, vals):
        self.filters.append((col, "in", list(vals)))
        return self

    def limit(self, n):
        return self

    def execute(self):
        return self.sb._execute(self)


class _TableHandle:
    def __init__(self, sb, name):
        self.sb = sb
        self.name = name

    def select(self, cols="*"):
        return _Query(self.sb, self.name, "select")

    def insert(self, row):
        return _Query(self.sb, self.name, "insert", payload=row)

    def upsert(self, row, on_conflict=None):
        return _Query(self.sb, self.name, "upsert", payload=row, on_conflict=on_conflict)

    def update(self, row):
        return _Query(self.sb, self.name, "update", payload=row)


class FakeSb:
    """In-memory fake mimicking the chained supabase-py query builder used
    throughout services/. Records reads/writes so tests can assert exact
    query counts (no N+1)."""

    def __init__(self, tables=None):
        self.tables = {k: list(v) for k, v in (tables or {}).items()}
        self.reads = []       # table names touched via .select()
        self.inserted = []    # (table, row)
        self.upserted = []    # (table, row, on_conflict)
        self.updated = []     # (table, payload, filters)
        self.raise_on_upsert = {}  # table -> exception to raise once
        self._next_id = 1000

    def table(self, name):
        return _TableHandle(self, name)

    def _new_id(self):
        self._next_id += 1
        return f"id-{self._next_id}"

    def _match(self, row, filters):
        for col, kind, val in filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "in" and row.get(col) not in val:
                return False
        return True

    def _execute(self, q):
        rows = self.tables.setdefault(q.table, [])
        if q.verb == "select":
            self.reads.append(q.table)
            matched = [r for r in rows if self._match(r, q.filters)]
            return _Result(matched)
        if q.verb == "insert":
            row = dict(q.payload)
            row.setdefault("id", self._new_id())
            rows.append(row)
            self.inserted.append((q.table, row))
            return _Result([row])
        if q.verb == "upsert":
            exc = self.raise_on_upsert.get(q.table)
            if exc is not None:
                self.raise_on_upsert[q.table] = None
                raise exc
            row = dict(q.payload)
            key_cols = [c.strip() for c in (q.on_conflict or "").split(",") if c.strip()]
            existing = None
            if key_cols:
                for r in rows:
                    if all(r.get(c) == row.get(c) for c in key_cols):
                        existing = r
                        break
            if existing is not None:
                existing.update(row)
                row = existing
            else:
                row.setdefault("id", self._new_id())
                rows.append(row)
            self.upserted.append((q.table, row, q.on_conflict))
            return _Result([row])
        if q.verb == "update":
            matched = [r for r in rows if self._match(r, q.filters)]
            for r in matched:
                r.update(q.payload)
            self.updated.append((q.table, q.payload, list(q.filters)))
            return _Result(matched)
        raise AssertionError(f"unhandled verb {q.verb}")


# ---------------------------------------------------------------------------
# submit_pick
# ---------------------------------------------------------------------------

def test_submit_pick_raises_when_team_already_used():
    sb = FakeSb({
        "survivor_picks": [
            {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A",
             "espn_game_id": "g1", "result": "win", "set_by": "member"},
        ],
    })
    entry = {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active"}
    with pytest.raises(TeamAlreadyUsed):
        submit_pick(sb, entry, week=2, team_ref="team-A", espn_game_id="g2")


def test_submit_pick_success_upserts_on_entry_week():
    sb = FakeSb({"survivor_picks": []})
    entry = {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active"}
    pick = submit_pick(sb, entry, week=1, team_ref="team-B", espn_game_id="g9")
    assert pick["entry_id"] == "e1"
    assert pick["week"] == 1
    assert pick["team_ref"] == "team-B"
    assert sb.upserted[0][2] == "entry_id,week"

    # Re-submitting a different team for the SAME week overwrites via upsert,
    # not a second row.
    pick2 = submit_pick(sb, entry, week=1, team_ref="team-C", espn_game_id="g10")
    assert pick2["team_ref"] == "team-C"
    assert len([r for r in sb.tables["survivor_picks"] if r["entry_id"] == "e1"]) == 1


def test_submit_pick_unchanged_resubmit_same_week_succeeds():
    # Re-submitting the SAME team_ref for the SAME week (optimistic-save
    # retry, double-tap, or just picking the same team again) must succeed:
    # UNIQUE(entry_id, team_ref) is only violated by OTHER rows, and the
    # upsert-on-(entry_id,week) re-upserts this exact row, not a new one.
    sb = FakeSb({
        "survivor_picks": [
            {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A",
             "espn_game_id": "g1", "result": "pending", "set_by": "member"},
        ],
    })
    entry = {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active"}
    pick = submit_pick(sb, entry, week=1, team_ref="team-A", espn_game_id="g1")
    assert pick["team_ref"] == "team-A"
    assert pick["week"] == 1
    assert len([r for r in sb.tables["survivor_picks"] if r["entry_id"] == "e1"]) == 1


def test_submit_pick_same_team_different_week_still_raises():
    # The guard against reusing a team across weeks must still hold.
    sb = FakeSb({
        "survivor_picks": [
            {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A",
             "espn_game_id": "g1", "result": "win", "set_by": "member"},
        ],
    })
    entry = {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active"}
    with pytest.raises(TeamAlreadyUsed):
        submit_pick(sb, entry, week=2, team_ref="team-A", espn_game_id="g2")


def test_submit_pick_race_condition_reraised_as_team_already_used():
    sb = FakeSb({"survivor_picks": []})
    sb.raise_on_upsert["survivor_picks"] = APIError({
        "code": "23505",
        "message": "duplicate key value violates unique constraint \"survivor_picks_entry_id_team_ref_key\"",
    })
    entry = {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active"}
    with pytest.raises(TeamAlreadyUsed):
        submit_pick(sb, entry, week=3, team_ref="team-D", espn_game_id="g3")


def test_submit_pick_non_unique_error_propagates():
    sb = FakeSb({"survivor_picks": []})
    sb.raise_on_upsert["survivor_picks"] = APIError({"code": "500", "message": "server exploded"})
    entry = {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active"}
    with pytest.raises(APIError):
        submit_pick(sb, entry, week=3, team_ref="team-E", espn_game_id="g3")


# ---------------------------------------------------------------------------
# get_or_create_entry
# ---------------------------------------------------------------------------

def test_get_or_create_entry_returns_existing():
    sb = FakeSb({"survivor_entries": [
        {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active", "eliminated_week": None},
    ]})
    entry = get_or_create_entry(sb, "pool1", "m1")
    assert entry["id"] == "e1"
    assert not sb.inserted


def test_get_or_create_entry_creates_when_missing():
    sb = FakeSb({"survivor_entries": []})
    entry = get_or_create_entry(sb, "pool1", "m1")
    assert entry["pool_id"] == "pool1"
    assert entry["member_id"] == "m1"
    assert entry["status"] == "active"
    assert sb.inserted[0][0] == "survivor_entries"


# ---------------------------------------------------------------------------
# record_buyback
# ---------------------------------------------------------------------------

def test_record_buyback_inserts_and_reactivates_entry():
    sb = FakeSb({
        "survivor_entries": [
            {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "eliminated", "eliminated_week": 4},
        ],
    })
    entry = sb.tables["survivor_entries"][0]
    record_buyback(sb, entry, week=6, kind="regular", fee=None)

    assert sb.inserted[0][0] == "survivor_buybacks"
    assert sb.inserted[0][1]["entry_id"] == "e1"
    assert sb.inserted[0][1]["week"] == 6
    assert sb.inserted[0][1]["kind"] == "regular"

    updated_entry = sb.tables["survivor_entries"][0]
    assert updated_entry["status"] == "active"


# ---------------------------------------------------------------------------
# board_data
# ---------------------------------------------------------------------------

def test_board_data_exactly_three_reads_and_assembles_shape():
    sb = FakeSb({
        "survivor_entries": [
            {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active",
             "eliminated_week": None,
             "pool_members": {"user_id": "u1", "users": {"display_name": "Alice"}}},
            {"id": "e2", "pool_id": "pool1", "member_id": "m2", "status": "eliminated",
             "eliminated_week": 3,
             "pool_members": {"user_id": "u2", "users": {"display_name": "Bob"}}},
        ],
        "survivor_picks": [
            {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A", "result": "win"},
            {"id": "p2", "entry_id": "e2", "week": 1, "team_ref": "team-B", "result": "loss"},
            {"id": "p3", "entry_id": "e1", "week": 2, "team_ref": "team-C", "result": "pending"},
        ],
        "survivor_buybacks": [
            {"id": "b1", "entry_id": "e2", "week": 6, "kind": "regular", "fee": None},
        ],
    })

    result = board_data(sb, "pool1")

    assert sb.reads == ["survivor_entries", "survivor_picks", "survivor_buybacks"]

    assert {e["id"] for e in result["entries"]} == {"e1", "e2"}
    assert result["picks"]["e1"][1]["team_ref"] == "team-A"
    assert result["picks"]["e1"][2]["team_ref"] == "team-C"
    assert result["picks"]["e2"][1]["team_ref"] == "team-B"
    assert result["weeks"] == [1, 2]


def test_board_data_empty_pool_short_circuits():
    sb = FakeSb({"survivor_entries": []})
    result = board_data(sb, "pool-empty")
    assert result == {"entries": [], "picks": {}, "weeks": []}
    # Only the entries read happened -- no point querying picks/buybacks for
    # an empty entry set.
    assert sb.reads == ["survivor_entries"]


# ---------------------------------------------------------------------------
# apply_resolution
# ---------------------------------------------------------------------------

def test_apply_resolution_writes_pick_result_and_entry_status():
    sb = FakeSb({
        "survivor_picks": [
            {"id": "p1", "entry_id": "e1", "week": 5, "team_ref": "team-A", "result": "pending"},
            {"id": "p2", "entry_id": "e2", "week": 5, "team_ref": "team-B", "result": "pending"},
        ],
        "survivor_entries": [
            {"id": "e1", "pool_id": "pool1", "member_id": "m1", "status": "active", "eliminated_week": None},
            {"id": "e2", "pool_id": "pool1", "member_id": "m2", "status": "active", "eliminated_week": None},
        ],
    })
    resolution = {
        "e1": {"result": "win", "status": "active", "eliminated_week": None},
        "e2": {"result": "loss", "status": "eliminated", "eliminated_week": 5},
    }
    apply_resolution(sb, resolution, week=5)

    picks_by_entry = {r["entry_id"]: r for r in sb.tables["survivor_picks"]}
    assert picks_by_entry["e1"]["result"] == "win"
    assert picks_by_entry["e2"]["result"] == "loss"

    entries_by_id = {r["id"]: r for r in sb.tables["survivor_entries"]}
    assert entries_by_id["e1"]["status"] == "active"
    assert entries_by_id["e2"]["status"] == "eliminated"
    assert entries_by_id["e2"]["eliminated_week"] == 5


# ---------------------------------------------------------------------------
# resolve_and_apply
# ---------------------------------------------------------------------------

def _survivor_pool_fixture():
    """One survivor pool, one competition, one fully-final week-1 game: team-A
    (home) beats team-B (away) 20-10. e1 picked team-A (survives), e2 picked
    team-B (eliminated)."""
    return FakeSb({
        "pool_competitions": [
            {"pool_id": "pool1", "competition_id": "comp1"},
        ],
        "teams": [
            {"id": "team-A", "competition_id": "comp1", "ext_id": "T-A"},
            {"id": "team-B", "competition_id": "comp1", "ext_id": "T-B"},
        ],
        "game_results": [
            {"espn_game_id": "g1", "week": 1, "competition_id": "comp1",
             "home_team_id": "T-A", "away_team_id": "T-B",
             "home_score": 20, "away_score": 10},
        ],
        "survivor_entries": [
            {"id": "e1", "pool_id": "pool1", "member_id": "m1",
             "status": "active", "eliminated_week": None},
            {"id": "e2", "pool_id": "pool1", "member_id": "m2",
             "status": "active", "eliminated_week": None},
        ],
        "survivor_picks": [
            {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A",
             "espn_game_id": "g1", "result": "pending"},
            {"id": "p2", "entry_id": "e2", "week": 1, "team_ref": "team-B",
             "espn_game_id": "g1", "result": "pending"},
        ],
    })


def test_resolve_and_apply_final_week_applies_win_and_loss():
    sb = _survivor_pool_fixture()
    pool = {"id": "pool1", "survivor_config": {}}

    result = resolve_and_apply(sb, pool)

    assert result[1]["e1"]["status"] == "active"
    assert result[1]["e2"]["status"] == "eliminated"
    assert result[1]["e2"]["eliminated_week"] == 1

    entries_by_id = {r["id"]: r for r in sb.tables["survivor_entries"]}
    assert entries_by_id["e1"]["status"] == "active"
    assert entries_by_id["e1"]["eliminated_week"] is None
    assert entries_by_id["e2"]["status"] == "eliminated"
    assert entries_by_id["e2"]["eliminated_week"] == 1

    picks_by_entry = {r["entry_id"]: r for r in sb.tables["survivor_picks"]}
    assert picks_by_entry["e1"]["result"] == "win"
    assert picks_by_entry["e2"]["result"] == "loss"


def test_resolve_and_apply_is_idempotent_on_second_call():
    # resolve_week only grades entries still "active", so an entry eliminated
    # on the first call correctly drops out of the second call's resolution
    # dict -- that's not oscillation, since its DB row is left untouched.
    # Idempotence means the *DB state* after two calls matches the state
    # after one, not that the raw resolution payload is byte-identical.
    sb = _survivor_pool_fixture()
    pool = {"id": "pool1", "survivor_config": {}}

    resolve_and_apply(sb, pool)
    entries_after_first = {r["id"]: dict(r) for r in sb.tables["survivor_entries"]}
    picks_after_first = {r["entry_id"]: dict(r) for r in sb.tables["survivor_picks"]}

    resolve_and_apply(sb, pool)
    entries_after_second = {r["id"]: dict(r) for r in sb.tables["survivor_entries"]}
    picks_after_second = {r["entry_id"]: dict(r) for r in sb.tables["survivor_picks"]}

    assert entries_after_second == entries_after_first
    assert picks_after_second == picks_after_first

    assert entries_after_second["e1"]["status"] == "active"
    assert entries_after_second["e2"]["status"] == "eliminated"
    assert entries_after_second["e2"]["eliminated_week"] == 1
