import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import re

import pytest
from unittest.mock import patch
from postgrest.exceptions import APIError
from app import create_app


@pytest.fixture
def authed_client():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-uuid"
        sess["display_name"] = "Test User"
    return client


# ---------------------------------------------------------------------------
# Generic in-memory Supabase double -- same recording-double shape used in
# tests/test_survivor_data.py, reused here so route tests exercise the real
# get_or_create_entry/submit_pick/record_buyback code paths instead of
# hand-mocking every query chain.
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

    def order(self, *a, **k):
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
    def __init__(self, tables=None):
        self.tables = {k: list(v) for k, v in (tables or {}).items()}
        self.raise_on_insert = {}  # table -> exception to raise once
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
            matched = [r for r in rows if self._match(r, q.filters)]
            return _Result(matched)
        if q.verb == "insert":
            exc = self.raise_on_insert.get(q.table)
            if exc is not None:
                self.raise_on_insert[q.table] = None
                raise exc
            row = dict(q.payload)
            row.setdefault("id", self._new_id())
            rows.append(row)
            return _Result([row])
        if q.verb == "upsert":
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
            return _Result([row])
        if q.verb == "update":
            matched = [r for r in rows if self._match(r, q.filters)]
            for r in matched:
                r.update(q.payload)
            return _Result(matched)
        raise AssertionError(f"unhandled verb {q.verb}")


def _base_tables(survivor_config=None, creator_id="creator-uuid"):
    return {
        "pools": [{"id": "pool-1", "creator_id": creator_id, "survivor_config": survivor_config or {}}],
        "pool_members": [{"id": "m1", "pool_id": "pool-1", "user_id": "test-uuid"}],
        "pool_competitions": [{"pool_id": "pool-1", "competition_id": "c1"}],
        "survivor_entries": [],
        "survivor_picks": [],
        "survivor_buybacks": [],
        "teams": [
            {"id": "team-A", "ext_id": "ext-A"},
            {"id": "team-B", "ext_id": "ext-B"},
            {"id": "team-other", "ext_id": "ext-other"},
        ],
    }


# ---------------------------------------------------------------------------
# pick: locked week -> 409
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_pick_on_locked_week_returns_409(mock_sb, authed_client):
    tables = _base_tables()
    tables["game_results"] = [
        {"espn_game_id": "g-locked", "competition_id": "c1", "week": 1,
         "kickoff_at": "2020-01-05T18:00:00+00:00",
         "home_team_id": "ext-A", "away_team_id": "ext-B"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/pick", json={
        "week": 1, "team_ref": "team-A", "espn_game_id": "g-locked",
    })
    assert resp.status_code == 409
    assert sb.tables["survivor_picks"] == []


# ---------------------------------------------------------------------------
# pick: open week -> 200 and persists
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_pick_on_open_week_returns_200_and_persists(mock_sb, authed_client):
    tables = _base_tables()
    tables["game_results"] = [
        {"espn_game_id": "g-open", "competition_id": "c1", "week": 1,
         "kickoff_at": "2099-01-04T18:00:00+00:00",
         "home_team_id": "ext-A", "away_team_id": "ext-B"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/pick", json={
        "week": 1, "team_ref": "team-A", "espn_game_id": "g-open",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["pick"]["team_ref"] == "team-A"

    assert len(sb.tables["survivor_picks"]) == 1
    assert sb.tables["survivor_picks"][0]["team_ref"] == "team-A"
    assert sb.tables["survivor_picks"][0]["week"] == 1
    # Entry auto-created for the member.
    assert len(sb.tables["survivor_entries"]) == 1
    assert sb.tables["survivor_entries"][0]["member_id"] == "m1"


# ---------------------------------------------------------------------------
# buyback: outside any window -> 400
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_buyback_outside_window_returns_400(mock_sb, authed_client):
    # Empty survivor_config -> buyback_option always returns kind=None.
    sb = FakeSb(_base_tables(survivor_config={}))
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/buyback", json={"week": 3})
    assert resp.status_code == 400
    assert sb.tables["survivor_buybacks"] == []


# ---------------------------------------------------------------------------
# buyback: active (not eliminated) entry, even during an open window -> 400
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_buyback_by_active_entry_returns_400(mock_sb, authed_client):
    tables = _base_tables(survivor_config={
        "regular_buyback": {"weeks": [2, 4], "fee": 100},
    })
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "active"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/buyback", json={"week": 3})
    assert resp.status_code == 400
    assert sb.tables["survivor_buybacks"] == []


# ---------------------------------------------------------------------------
# buyback: eliminated entry, open regular window -> 200, row written, entry
# flips back to active
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_buyback_by_eliminated_entry_returns_200(mock_sb, authed_client):
    tables = _base_tables(survivor_config={
        "regular_buyback": {"weeks": [2, 4], "fee": 100},
    })
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "eliminated",
         "eliminated_week": 2},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/buyback", json={"week": 3})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True

    assert len(sb.tables["survivor_buybacks"]) == 1
    assert sb.tables["survivor_buybacks"][0]["kind"] == "regular"
    assert sb.tables["survivor_entries"][0]["status"] == "active"


# ---------------------------------------------------------------------------
# pick: team_ref not one of the espn_game_id game's two teams -> 400
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_pick_team_not_in_game_returns_400(mock_sb, authed_client):
    tables = _base_tables()
    tables["game_results"] = [
        {"espn_game_id": "g-open", "competition_id": "c1", "week": 1,
         "kickoff_at": "2099-01-04T18:00:00+00:00",
         "home_team_id": "ext-A", "away_team_id": "ext-B"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/pick", json={
        "week": 1, "team_ref": "team-other", "espn_game_id": "g-open",
    })
    assert resp.status_code == 400
    assert sb.tables["survivor_picks"] == []
    assert sb.tables["survivor_entries"] == []


# ---------------------------------------------------------------------------
# Commissioner: assign_pick
# ---------------------------------------------------------------------------

def _locked_game_tables(creator_id="test-uuid"):
    """A game whose kickoff (and week-Sunday, since it's the only game that
    week) is well in the past -- is_locked() would be True for a member pick,
    but assign_pick doesn't consult the lock at all."""
    tables = _base_tables(creator_id=creator_id)
    tables["game_results"] = [
        {"espn_game_id": "g-locked", "competition_id": "c1", "week": 1,
         "kickoff_at": "2020-01-05T18:00:00+00:00",
         "home_team_id": "ext-A", "away_team_id": "ext-B"},
    ]
    tables["pool_members"].append(
        {"id": "m-target", "pool_id": "pool-1", "user_id": "member-uuid"}
    )
    return tables


@patch("routes.survivor.get_service_client")
def test_assign_pick_by_creator_succeeds_on_locked_week(mock_sb, authed_client):
    # authed_client's session user is "test-uuid" -- make it the pool creator.
    sb = FakeSb(_locked_game_tables(creator_id="test-uuid"))
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/assign-pick", json={
        "member_id": "m-target", "week": 1,
        "team_ref": "team-A", "espn_game_id": "g-locked",
        "note": "texted it in",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["pick"]["team_ref"] == "team-A"
    assert body["pick"]["set_by"] == "commissioner"
    assert body["pick"]["override_note"] == "texted it in"

    assert len(sb.tables["survivor_picks"]) == 1
    assert len(sb.tables["survivor_entries"]) == 1
    assert sb.tables["survivor_entries"][0]["member_id"] == "m-target"


@patch("routes.survivor.get_service_client")
def test_assign_pick_by_non_creator_returns_403(mock_sb, authed_client):
    # Pool creator is someone else -- authed_client's "test-uuid" is only a
    # regular member.
    sb = FakeSb(_locked_game_tables(creator_id="someone-else-uuid"))
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/assign-pick", json={
        "member_id": "m-target", "week": 1,
        "team_ref": "team-A", "espn_game_id": "g-locked",
    })
    assert resp.status_code == 403
    assert sb.tables["survivor_picks"] == []


@patch("routes.survivor.get_service_client")
def test_assign_pick_with_already_used_team_returns_400(mock_sb, authed_client):
    tables = _locked_game_tables(creator_id="test-uuid")
    tables["survivor_entries"] = [
        {"id": "e-target", "pool_id": "pool-1", "member_id": "m-target", "status": "active"},
    ]
    tables["survivor_picks"] = [
        {"id": "p1", "entry_id": "e-target", "week": 2, "team_ref": "team-A",
         "espn_game_id": "g-other", "set_by": "member", "override_note": None},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/assign-pick", json={
        "member_id": "m-target", "week": 1,
        "team_ref": "team-A", "espn_game_id": "g-locked",
    })
    assert resp.status_code == 400
    # Week 2's pick is untouched -- no new row was written for week 1.
    assert len(sb.tables["survivor_picks"]) == 1
    assert sb.tables["survivor_picks"][0]["week"] == 2


# ---------------------------------------------------------------------------
# Commissioner: record_buyback_for
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_buyback_for_by_creator_bypasses_window_and_reinstates(mock_sb, authed_client):
    tables = _base_tables(creator_id="test-uuid")
    tables["pool_members"].append(
        {"id": "m-target", "pool_id": "pool-1", "user_id": "member-uuid"}
    )
    tables["survivor_entries"] = [
        {"id": "e-target", "pool_id": "pool-1", "member_id": "m-target", "status": "eliminated"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/buyback-for", json={
        "member_id": "m-target", "week": 20, "kind": "regular", "fee": 0,
    })
    assert resp.status_code == 200
    assert len(sb.tables["survivor_buybacks"]) == 1
    assert sb.tables["survivor_entries"][0]["status"] == "active"


# ---------------------------------------------------------------------------
# Commissioner: set_status
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_set_status_eliminates_entry(mock_sb, authed_client):
    tables = _base_tables(creator_id="test-uuid")
    tables["pool_members"].append(
        {"id": "m-target", "pool_id": "pool-1", "user_id": "member-uuid"}
    )
    tables["survivor_entries"] = [
        {"id": "e-target", "pool_id": "pool-1", "member_id": "m-target", "status": "active"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/set-status", json={
        "member_id": "m-target", "status": "eliminated", "eliminated_week": 5,
    })
    assert resp.status_code == 200
    assert sb.tables["survivor_entries"][0]["status"] == "eliminated"
    assert sb.tables["survivor_entries"][0]["eliminated_week"] == 5


@patch("routes.survivor.get_service_client")
def test_set_status_reinstate_advances_active_from_week(mock_sb, authed_client):
    # Reinstating an eliminated entry must advance active_from_week to the
    # given week so a later re-resolution can't re-eliminate it on the loss
    # it was reinstated past.
    tables = _base_tables(creator_id="test-uuid")
    tables["pool_members"].append(
        {"id": "m-target", "pool_id": "pool-1", "user_id": "member-uuid"}
    )
    tables["survivor_entries"] = [
        {"id": "e-target", "pool_id": "pool-1", "member_id": "m-target",
         "status": "eliminated", "eliminated_week": 5, "active_from_week": 1},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/set-status", json={
        "member_id": "m-target", "status": "active", "week": 6,
    })
    assert resp.status_code == 200
    entry = sb.tables["survivor_entries"][0]
    assert entry["status"] == "active"
    assert entry["eliminated_week"] is None
    assert entry["active_from_week"] == 6


@patch("routes.survivor.get_service_client")
def test_buyback_for_duplicate_super_returns_400(mock_sb, authed_client):
    # A second super buyback trips the uniq_super_buyback unique index; the
    # commissioner path (which skips the member-side limit check) must map the
    # 23505 violation to a clean 400 instead of a 500.
    tables = _base_tables(creator_id="test-uuid")
    tables["pool_members"].append(
        {"id": "m-target", "pool_id": "pool-1", "user_id": "member-uuid"}
    )
    tables["survivor_entries"] = [
        {"id": "e-target", "pool_id": "pool-1", "member_id": "m-target", "status": "eliminated"},
    ]
    sb = FakeSb(tables)
    sb.raise_on_insert["survivor_buybacks"] = APIError({
        "code": "23505",
        "message": "duplicate key value violates unique constraint \"uniq_super_buyback\"",
    })
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/buyback-for", json={
        "member_id": "m-target", "week": 9, "kind": "super", "fee": 500,
    })
    assert resp.status_code == 400
    assert "already used" in resp.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# Commissioner: resolve_now
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_resolve_now_eliminates_loser_and_grades_winner(mock_sb, authed_client):
    tables = _base_tables(creator_id="test-uuid")
    tables["survivor_entries"] = [
        {"id": "e-win", "pool_id": "pool-1", "member_id": "m1", "status": "active"},
        {"id": "e-lose", "pool_id": "pool-1", "member_id": "m1", "status": "active"},
    ]
    tables["survivor_picks"] = [
        {"id": "p-win", "entry_id": "e-win", "week": 1, "team_ref": "team-A", "espn_game_id": "g1"},
        {"id": "p-lose", "entry_id": "e-lose", "week": 1, "team_ref": "team-B", "espn_game_id": "g1"},
    ]
    tables["game_results"] = [
        {"espn_game_id": "g1", "week": 1, "home_team_id": "ext-A", "away_team_id": "ext-B",
         "home_score": 24, "away_score": 10, "winner_team_id": "ext-A"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/resolve", json={"week": 1})
    assert resp.status_code == 200

    by_id = {e["id"]: e for e in sb.tables["survivor_entries"]}
    assert by_id["e-win"]["status"] == "active"
    assert by_id["e-lose"]["status"] == "eliminated"
    assert by_id["e-lose"]["eliminated_week"] == 1


# ---------------------------------------------------------------------------
# Commissioner: settle_season
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GET board: renders the members x weeks grid (Task 12)
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_survivor_board_renders_grid_with_alive_count_and_out_label(mock_sb, authed_client):
    tables = _base_tables()
    tables["teams"] = [
        {"id": "team-A", "ext_id": "ext-A", "abbreviation": "KC"},
        {"id": "team-B", "ext_id": "ext-B", "abbreviation": "BUF"},
    ]
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "u1", "users": {"display_name": "Alice"}}},
        {"id": "e2", "pool_id": "pool-1", "member_id": "m2", "status": "eliminated",
         "eliminated_week": 2,
         "pool_members": {"user_id": "u2", "users": {"display_name": "Bob"}}},
    ]
    tables["survivor_picks"] = [
        {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A", "result": "win", "set_by": "member"},
        {"id": "p2", "entry_id": "e2", "week": 1, "team_ref": "team-B", "result": "loss", "set_by": "member"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Header alive count: 1 of 2 entries is still active.
    assert "1 of 2 alive" in html
    # At least one team abbreviation cell rendered.
    assert "KC" in html
    # Eliminated entry's row shows an OUT status label.
    assert "OUT" in html
    assert "Wk 2" in html


@patch("routes.survivor.get_service_client")
def test_survivor_board_hides_current_week_picks_until_lock(mock_sb, authed_client):
    """The current (not-yet-locked) week column shows the lock glyph instead
    of leaking a pick -- here week 2 has no game_results yet, so
    _week_lock_at can't resolve a lock instant and the board conservatively
    treats it as locked/hidden. The viewing session ("test-uuid") is a third
    party with no entry of their own in this pool -- distinct from the
    entry's owner ("u1") -- so the "always see your own pick" exception
    doesn't apply here and the lock glyph is what should render."""
    tables = _base_tables()
    tables["teams"] = [{"id": "team-A", "ext_id": "ext-A", "abbreviation": "KC"}]
    tables["pool_members"] = [
        {"id": "m1", "pool_id": "pool-1", "user_id": "u1"},
    ]
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "u1", "users": {"display_name": "Alice"}}},
    ]
    tables["survivor_picks"] = [
        {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A", "result": "win", "set_by": "member"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "\U0001F512" in html  # lock glyph for the hidden current week


@patch("routes.survivor.get_service_client")
def test_survivor_board_hides_other_members_early_week_pick_before_lock(mock_sb, authed_client):
    """Reproduces the board-leak bug: week 1 is fully graded for both
    members, then Alice submits her week-2 pick while week 2 is still
    before its own lock. The old code gated hiding on
    `current_week = max(weeks_with_picks) + 1`, so Alice's early pick
    bumped weeks_with_picks to include week 2, which flipped week 2 from
    "current/hidden" to "past/revealed" for EVERYONE -- Bob could see
    Alice's week-2 team days before lock. Hiding must instead be keyed off
    week 2's own lock instant, and Alice must still see her own pick."""
    tables = _base_tables()
    tables["teams"] = [
        {"id": "team-A", "ext_id": "ext-A", "abbreviation": "KC"},
        {"id": "team-B", "ext_id": "ext-B", "abbreviation": "BUF"},
        {"id": "team-C", "ext_id": "ext-C", "abbreviation": "DAL"},
        {"id": "team-D", "ext_id": "ext-D", "abbreviation": "PHI"},
    ]
    tables["pool_members"] = [
        {"id": "m1", "pool_id": "pool-1", "user_id": "alice-uuid"},
        {"id": "m2", "pool_id": "pool-1", "user_id": "bob-uuid"},
    ]
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "alice-uuid", "users": {"display_name": "Alice"}}},
        {"id": "e2", "pool_id": "pool-1", "member_id": "m2", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "bob-uuid", "users": {"display_name": "Bob"}}},
    ]
    tables["survivor_picks"] = [
        # Week 1 -- fully graded, safely in the past.
        {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A", "result": "win", "set_by": "member"},
        {"id": "p2", "entry_id": "e2", "week": 1, "team_ref": "team-B", "result": "loss", "set_by": "member"},
        # Week 2 -- only Alice has picked, and week 2 hasn't locked yet.
        {"id": "p3", "entry_id": "e1", "week": 2, "team_ref": "team-C", "result": None, "set_by": "member"},
    ]
    tables["game_results"] = [
        # Week 1 kicked off well in the past -> week 1 is locked/revealed.
        {"espn_game_id": "g1", "competition_id": "c1", "week": 1,
         "kickoff_at": "2020-01-05T18:00:00+00:00",
         "home_team_id": "ext-A", "away_team_id": "ext-B"},
        # Week 2 kicks off far in the future -> week 2 is NOT locked yet.
        {"espn_game_id": "g2", "competition_id": "c1", "week": 2,
         "kickoff_at": "2099-01-04T18:00:00+00:00",
         "home_team_id": "ext-C", "away_team_id": "ext-D"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    # Bob loads the board: must NOT see Alice's week-2 pick.
    with authed_client.session_transaction() as sess:
        sess["user_id"] = "bob-uuid"
    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    bob_html = resp.get_data(as_text=True)
    assert "DAL" not in bob_html, "week-2 pick leaked to another member before lock"

    # Alice loads the board: she DOES see her own week-2 pick.
    with authed_client.session_transaction() as sess:
        sess["user_id"] = "alice-uuid"
    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    alice_html = resp.get_data(as_text=True)
    assert "DAL" in alice_html


@patch("routes.survivor.get_service_client")
def test_settle_season_marks_pool_complete_and_records_winners(mock_sb, authed_client):
    tables = _base_tables(creator_id="test-uuid")
    tables["survivor_entries"] = [
        {"id": "e-win", "pool_id": "pool-1", "member_id": "m1", "status": "active"},
        {"id": "e-out", "pool_id": "pool-1", "member_id": "m1", "status": "eliminated"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.post("/pool/pool-1/survivor/settle", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["winner_entry_ids"] == ["e-win"]
    assert sb.tables["pools"][0]["draft_status"] == "complete"
    assert sb.tables["pools"][0]["survivor_config"]["winner_entry_ids"] == ["e-win"]


# ---------------------------------------------------------------------------
# GET pick view (Task 13): renders this week's games with logos + spreads,
# marks an already-used team, and shows the member's current pick.
# ---------------------------------------------------------------------------

def _pick_view_tables():
    tables = _base_tables()
    tables["competitions"] = [{"id": "c1", "league": "nfl", "season": 2026}]
    tables["teams"] = [
        {"id": "team-pit", "competition_id": "c1", "ext_id": 23,
         "name": "Pittsburgh Steelers", "abbreviation": "PIT"},
        {"id": "team-nyj", "competition_id": "c1", "ext_id": 20,
         "name": "New York Jets", "abbreviation": "NYJ"},
        {"id": "team-kc", "competition_id": "c1", "ext_id": 12,
         "name": "Kansas City Chiefs", "abbreviation": "KC"},
        {"id": "team-lv", "competition_id": "c1", "ext_id": 13,
         "name": "Las Vegas Raiders", "abbreviation": "LV"},
    ]
    # Far-future kickoffs so week 5 resolves as the current (not-yet-locked) week.
    tables["game_results"] = [
        {"espn_game_id": "g-pit-nyj", "competition_id": "c1", "week": 5,
         "kickoff_at": "2099-01-04T18:00:00+00:00",
         "home_team_id": 23, "away_team_id": 20},
        {"espn_game_id": "g-kc-lv", "competition_id": "c1", "week": 5,
         "kickoff_at": "2099-01-05T18:00:00+00:00",
         "home_team_id": 12, "away_team_id": 13},
    ]
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "active"},
    ]
    # Chiefs already used in an earlier week -> should render greyed/used on
    # this week's board even though they're playing again.
    tables["survivor_picks"] = [
        {"id": "p-old", "entry_id": "e1", "week": 2, "team_ref": "team-kc",
         "espn_game_id": "g-old", "result": "win", "set_by": "member"},
    ]
    return tables


_FAKE_ODDS_EVENT = {
    "home_team": "Pittsburgh Steelers",
    "away_team": "New York Jets",
    "bookmakers": [{
        "key": "fanduel", "title": "FanDuel",
        "markets": [{
            "key": "spreads",
            "outcomes": [
                {"name": "Pittsburgh Steelers", "price": -150, "point": -3.0},
                {"name": "New York Jets", "price": 130, "point": 3.0},
            ],
        }],
    }],
}


@patch("services.odds.fetch_odds")
@patch("routes.survivor.get_service_client")
def test_survivor_pick_view_renders_logo_spread_and_used_team(mock_sb, mock_fetch_odds, authed_client):
    sb = FakeSb(_pick_view_tables())
    mock_sb.return_value = sb
    mock_fetch_odds.return_value = [_FAKE_ODDS_EVENT]

    resp = authed_client.get("/pool/pool-1/survivor/pick")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Team nickname + real ESPN logo URL rendered.
    assert "Steelers" in html
    assert "espncdn.com/i/teamlogos/nfl/500/23.png" in html
    # Spread value from the (mocked) odds event rendered somewhere on the page.
    assert "3.0" in html
    # Chiefs were used in week 2 -- greyed/disabled and tagged with the week used.
    assert "spick-used" in html
    assert "used W2" in html


# ---------------------------------------------------------------------------
# GET board (Task 14): commissioner panel renders only for the pool creator.
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_survivor_board_shows_commish_panel_to_creator_only(mock_sb, authed_client):
    tables = _base_tables(creator_id="test-uuid")
    tables["pool_members"].append(
        {"id": "m2", "pool_id": "pool-1", "user_id": "member-uuid"}
    )
    tables["users"] = [
        {"id": "test-uuid", "display_name": "Commish"},
        {"id": "member-uuid", "display_name": "Member Two"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    # Creator (session user_id == pool creator_id == "test-uuid") sees the
    # commissioner panel, with the Assign Pick and Settle Season controls.
    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    creator_html = resp.get_data(as_text=True)
    assert "commish-panel" in creator_html
    assert "commish-assign-member" in creator_html
    assert "Settle season" in creator_html

    # A regular (non-creator) member does NOT see the panel at all.
    with authed_client.session_transaction() as sess:
        sess["user_id"] = "member-uuid"
    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    member_html = resp.get_data(as_text=True)
    assert "commish-panel" not in member_html
    assert "Settle season" not in member_html


@patch("services.odds.fetch_odds")
@patch("routes.survivor.get_service_client")
def test_survivor_pick_json_matches_view_data(mock_sb, mock_fetch_odds, authed_client):
    sb = FakeSb(_pick_view_tables())
    mock_sb.return_value = sb
    mock_fetch_odds.return_value = [_FAKE_ODDS_EVENT]

    resp = authed_client.get("/pool/pool-1/survivor/pick.json")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["week"] == 5
    assert len(body["games"]) == 2
    used_refs = {
        g[side]["team_ref"]: g[side]["used_week"]
        for g in body["games"] for side in ("home", "away")
    }
    assert used_refs["team-kc"] == 2


# ---------------------------------------------------------------------------
# Player Results: heading, inline expand, rules panel (Task 15)
# ---------------------------------------------------------------------------

@patch("routes.survivor.get_service_client")
def test_survivor_board_shows_player_results_heading(mock_sb, authed_client):
    tables = _base_tables()
    tables["teams"] = [
        {"id": "team-A", "ext_id": "ext-A", "abbreviation": "KC"},
        {"id": "team-B", "ext_id": "ext-B", "abbreviation": "BUF"},
    ]
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "u1", "users": {"display_name": "Alice"}}},
        {"id": "e2", "pool_id": "pool-1", "member_id": "m2", "status": "eliminated",
         "eliminated_week": 2,
         "pool_members": {"user_id": "u2", "users": {"display_name": "Bob"}}},
    ]
    tables["survivor_picks"] = [
        {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A", "result": "win", "set_by": "member"},
        {"id": "p2", "entry_id": "e2", "week": 1, "team_ref": "team-B", "result": "loss", "set_by": "member"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Player Results" in html
    # The pick UI is now embedded inline (Task: inline pick) -- the old
    # separate "Make your pick ->" link to a standalone page is gone.
    assert "survivor-pick-link" not in html
    assert "/survivor/pick\">Make your pick" not in html


@patch("routes.survivor.get_service_client")
def test_survivor_board_shows_rules_panel(mock_sb, authed_client):
    tables = _base_tables(survivor_config={
        "tie_is_win": True,
        "mercy_after_week": 7,
        "regular_buyback": {"weeks": [1, 6], "limit": None, "deadline": "sunday_1pm"},
        "super_buyback": {"weeks": [7, 17], "limit": 1, "fee": 500, "deadline": "friday_2359_et"},
    })
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Rules" in html
    assert "Super BuyBack" in html
    assert "survivor-rules-panel" in html


@patch("routes.survivor.get_service_client")
def test_survivor_board_buyback_summary_counts(mock_sb, authed_client):
    """The Player Results expand detail's buyback summary lines
    ("Super BuyBack: used/available", "Regular buybacks used: N") are
    computed in survivor_board() from entry["buybacks"] (super_used =
    any 'super' kind, regular_count = count of 'regular' kind). This
    exercises that computation end to end for three entries:
    - Alice: one regular buyback -> Super BuyBack available, regular=1.
    - Bob: one super buyback -> Super BuyBack used, regular=0.
    - Carol: no buybacks at all -> Super BuyBack available, regular=0.
    """
    tables = _base_tables()
    tables["teams"] = [
        {"id": "team-A", "ext_id": "ext-A", "abbreviation": "KC"},
    ]
    tables["pool_members"] = [
        {"id": "m1", "pool_id": "pool-1", "user_id": "test-uuid"},
    ]
    tables["survivor_entries"] = [
        {"id": "e-alice", "pool_id": "pool-1", "member_id": "m1", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "alice-uuid", "users": {"display_name": "Alice"}}},
        {"id": "e-bob", "pool_id": "pool-1", "member_id": "m2", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "bob-uuid", "users": {"display_name": "Bob"}}},
        {"id": "e-carol", "pool_id": "pool-1", "member_id": "m3", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "carol-uuid", "users": {"display_name": "Carol"}}},
    ]
    tables["survivor_buybacks"] = [
        {"id": "bb-reg", "entry_id": "e-alice", "week": 3, "kind": "regular"},
        {"id": "bb-sup", "entry_id": "e-bob", "week": 9, "kind": "super"},
        # Carol has no buyback rows at all.
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    def _detail_block(entry_id):
        m = re.search(r'id="sb-detail-%s".*?</tr>' % re.escape(entry_id), html, re.S)
        assert m, f"no expand-detail row found for {entry_id}"
        return m.group(0)

    alice_block = _detail_block("e-alice")
    assert "Super BuyBack: available" in alice_block
    assert "Regular buybacks used: 1" in alice_block

    bob_block = _detail_block("e-bob")
    assert "Super BuyBack: used" in bob_block
    assert "Regular buybacks used: 0" in bob_block

    carol_block = _detail_block("e-carol")
    assert "Super BuyBack: available" in carol_block
    assert "Regular buybacks used: 0" in carol_block


@patch("routes.survivor.get_service_client")
def test_survivor_board_expand_detail_hides_other_members_unlocked_pick(mock_sb, authed_client):
    """Mirrors test_survivor_board_hides_other_members_early_week_pick_before_lock,
    but asserts the fairness rule also holds for the new inline per-player
    expand detail (logos + week-by-week breakdown), not just the grid cell.
    Bob must never see Alice's unlocked week-2 team -- abbreviation OR logo
    URL -- anywhere in the Player Results section (grid + expand row).
    Assertions are scoped to the Player Results section rather than the
    whole page: the board also now embeds Bob's OWN "this week's pick" UI
    above Player Results, which legitimately shows week 2's matchup (team-C
    vs team-D, with both teams' logos) so Bob can make his own pick --
    that's public schedule data, not Alice's pick, so it's fine for it to
    appear up there. Alice must see her own pick in Player Results too."""
    tables = _base_tables()
    tables["teams"] = [
        {"id": "team-A", "ext_id": "ext-A", "abbreviation": "KC"},
        {"id": "team-B", "ext_id": "ext-B", "abbreviation": "BUF"},
        {"id": "team-C", "ext_id": "ext-C", "abbreviation": "DAL"},
        {"id": "team-D", "ext_id": "ext-D", "abbreviation": "PHI"},
    ]
    tables["pool_members"] = [
        {"id": "m1", "pool_id": "pool-1", "user_id": "alice-uuid"},
        {"id": "m2", "pool_id": "pool-1", "user_id": "bob-uuid"},
    ]
    tables["survivor_entries"] = [
        {"id": "e1", "pool_id": "pool-1", "member_id": "m1", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "alice-uuid", "users": {"display_name": "Alice"}}},
        {"id": "e2", "pool_id": "pool-1", "member_id": "m2", "status": "active",
         "eliminated_week": None,
         "pool_members": {"user_id": "bob-uuid", "users": {"display_name": "Bob"}}},
    ]
    tables["survivor_picks"] = [
        # Week 1 -- fully graded, safely in the past.
        {"id": "p1", "entry_id": "e1", "week": 1, "team_ref": "team-A", "result": "win", "set_by": "member"},
        {"id": "p2", "entry_id": "e2", "week": 1, "team_ref": "team-B", "result": "loss", "set_by": "member"},
        # Week 2 -- only Alice has picked, and week 2 hasn't locked yet.
        {"id": "p3", "entry_id": "e1", "week": 2, "team_ref": "team-C", "result": None, "set_by": "member"},
    ]
    tables["game_results"] = [
        {"espn_game_id": "g1", "competition_id": "c1", "week": 1,
         "kickoff_at": "2020-01-05T18:00:00+00:00",
         "home_team_id": "ext-A", "away_team_id": "ext-B"},
        {"espn_game_id": "g2", "competition_id": "c1", "week": 2,
         "kickoff_at": "2099-01-04T18:00:00+00:00",
         "home_team_id": "ext-C", "away_team_id": "ext-D"},
    ]
    tables["survivor_buybacks"] = [
        {"id": "bb1", "entry_id": "e1", "week": 1, "kind": "regular"},
    ]
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    # Bob loads the board: must NOT see Alice's week-2 team, in any form.
    with authed_client.session_transaction() as sess:
        sess["user_id"] = "bob-uuid"
    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    bob_html = resp.get_data(as_text=True)
    bob_player_results = bob_html.split("Player Results", 1)[1]
    assert "DAL" not in bob_player_results, "week-2 pick leaked to another member before lock"
    assert "teamlogos/nfl/500/ext-C" not in bob_player_results, "week-2 logo leaked to another member before lock"

    # Alice loads the board: she DOES see her own week-2 pick, in the grid
    # and (once expanded) in her own detail row.
    with authed_client.session_transaction() as sess:
        sess["user_id"] = "alice-uuid"
    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    alice_html = resp.get_data(as_text=True)
    alice_player_results = alice_html.split("Player Results", 1)[1]
    assert "DAL" in alice_player_results
    assert "teamlogos/nfl/500/ext-C" in alice_player_results


# ---------------------------------------------------------------------------
# Inline pick embedding: the board page now embeds the viewer's own weekly
# pick UI above Player Results instead of linking out to a separate page.
# ---------------------------------------------------------------------------

@patch("services.odds.fetch_odds")
@patch("routes.survivor.get_service_client")
def test_survivor_board_embeds_pick_section_above_player_results(mock_sb, mock_fetch_odds, authed_client):
    sb = FakeSb(_pick_view_tables())
    mock_sb.return_value = sb
    mock_fetch_odds.return_value = [_FAKE_ODDS_EVENT]

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "spick-lockbar" in html
    assert "Player Results" in html
    # Embedded ABOVE Player Results, per the spec.
    assert html.index("spick-lockbar") < html.index("Player Results")
    # Pick UI itself carries this week's real game content.
    assert "Steelers" in html


@patch("services.odds.fetch_odds")
@patch("routes.survivor.get_service_client")
def test_survivor_board_includes_survivor_js_and_initial_pick_data(mock_sb, mock_fetch_odds, authed_client):
    sb = FakeSb(_pick_view_tables())
    mock_sb.return_value = sb
    mock_fetch_odds.return_value = [_FAKE_ODDS_EVENT]

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert '/static/js/survivor.js' in html
    assert "INITIAL_PICK_DATA" in html
    assert "POOL_ID" in html
    assert re.search(r'"week":\s*5', html), "INITIAL_PICK_DATA should carry the resolved current week"


@patch("routes.survivor.get_service_client")
def test_survivor_board_pick_section_shows_no_games_state(mock_sb, authed_client):
    """When no games have been synced for the current week yet (the normal
    local/pre-season state), the embedded section must show the friendly
    empty state rather than erroring, and Player Results still renders
    below it."""
    tables = _base_tables()  # no game_results at all
    sb = FakeSb(tables)
    mock_sb.return_value = sb

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "No games scheduled yet" in html
    assert "Player Results" in html


@patch("services.odds.fetch_odds")
@patch("routes.survivor.get_service_client")
def test_standalone_survivor_pick_page_still_renders(mock_sb, mock_fetch_odds, authed_client):
    """The old standalone /survivor/pick page keeps working as a harmless
    fallback after the board absorbed its markup into a shared partial."""
    sb = FakeSb(_pick_view_tables())
    mock_sb.return_value = sb
    mock_fetch_odds.return_value = [_FAKE_ODDS_EVENT]

    resp = authed_client.get("/pool/pool-1/survivor/pick")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "spick-lockbar" in html
    assert "Steelers" in html


# ---------------------------------------------------------------------------
# Bug fix regression: commish panel + embedded pick section both render for
# a creator who is also a member (the common case). Both partials used to
# emit their own top-level `const POOL_ID = ...`, and two `const` decls of
# the same identifier in sibling <script> tags throw a SyntaxError that
# silently kills CURRENT_WEEK/INITIAL_PICK_DATA, breaking the pick UI. Now
# both partials assign `window.POOL_ID` (idempotent), so no collision.
# ---------------------------------------------------------------------------

@patch("services.odds.fetch_odds")
@patch("routes.survivor.get_service_client")
def test_survivor_board_creator_and_member_has_no_duplicate_pool_id_decl(mock_sb, mock_fetch_odds, authed_client):
    tables = _pick_view_tables()
    # authed_client's session user is "test-uuid", already a member via
    # pool_members m1 (see _base_tables) -- make them the creator too, so
    # both the commish panel AND the embedded pick section render together.
    tables["pools"][0]["creator_id"] = "test-uuid"
    sb = FakeSb(tables)
    mock_sb.return_value = sb
    mock_fetch_odds.return_value = [_FAKE_ODDS_EVENT]

    resp = authed_client.get("/pool/pool-1/survivor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Both partials rendered together.
    assert "commish-panel" in html
    assert "spick-lockbar" in html

    # No top-level `const POOL_ID` declarations left anywhere -- both
    # partials now use the collision-safe `window.POOL_ID` assignment.
    assert html.count("const POOL_ID") == 0
    assert "window.POOL_ID" in html
