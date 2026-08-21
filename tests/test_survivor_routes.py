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


def _base_tables(survivor_config=None):
    return {
        "pools": [{"id": "pool-1", "survivor_config": survivor_config or {}}],
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
