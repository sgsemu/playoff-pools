import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest
from unittest.mock import patch
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
