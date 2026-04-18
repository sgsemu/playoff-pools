import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import pytest
from unittest.mock import patch, MagicMock
from app import create_app


@pytest.fixture
def authed_client():
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user-1"
        sess["display_name"] = "Test User"
    return client


def _mock_pool(pool_type="draft", draft_status="active", draft_mode="live"):
    return {
        "id": "pool-1", "type": pool_type, "draft_status": draft_status,
        "draft_mode": draft_mode, "timer_seconds": 60, "name": "Test Pool",
        "scoring_config": {}, "auction_config": {}, "creator_id": "user-1"
    }


@patch("routes.draft.get_service_client")
def test_draft_room_loads(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    # Pool lookup
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool()]
    # Members
    mock_table.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []

    resp = authed_client.get("/pool/pool-1/draft")
    assert resp.status_code == 200


def _pick_tables_side_effect(pool, members, picks, nba_teams=None, nhl_teams=None):
    """Per-table mock factory for make_pick tests.

    make_pick touches pools, pool_members (by user_id and ordered by joined_at),
    draft_picks, and both team tables. Returning the right shape per table is
    easier than threading chained return_values.
    """
    nba_teams = nba_teams or []
    nhl_teams = nhl_teams or []

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
                m for m in members if m.get("user_id") == "user-1"
            ]
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = members
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = picks
            t.insert.return_value.execute.return_value.data = [{"id": "pick-new"}]
        elif name == "nba_teams":
            t.select.return_value.order.return_value.execute.return_value.data = nba_teams
        elif name == "nhl_teams":
            t.select.return_value.order.return_value.execute.return_value.data = nhl_teams
        return t

    return _side_effect


@patch("routes.draft.get_service_client")
def test_make_pick(mock_sb, authed_client):
    pool = _mock_pool()
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]
    mock_sb.return_value.table.side_effect = _pick_tables_side_effect(
        pool, members, picks=[], nba_teams=[{"id": 1, "name": "A", "seed": 1}],
    )

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"nba_team_id": 1, "league": "nba"})
    assert resp.status_code == 200


@patch("routes.draft.get_service_client")
def test_cannot_pick_already_taken_team(mock_sb, authed_client):
    pool = _mock_pool()
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]
    picks = [{"nba_team_id": 1, "team_id": 1, "league": "nba", "member_id": "member-2", "pick_order": 1, "round": 1}]
    mock_sb.return_value.table.side_effect = _pick_tables_side_effect(
        pool, members, picks, nba_teams=[{"id": 1, "name": "A", "seed": 1}],
    )

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"nba_team_id": 1, "league": "nba"})
    assert resp.status_code == 400


@patch("routes.draft.get_service_client")
def test_make_pick_rejects_inactive_draft(mock_sb, authed_client):
    pool = _mock_pool(draft_status="pending")
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]
    mock_sb.return_value.table.side_effect = _pick_tables_side_effect(pool, members, picks=[])

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"nba_team_id": 1, "league": "nba"})
    assert resp.status_code == 409


@patch("routes.draft.get_service_client")
def test_make_pick_rejects_out_of_turn(mock_sb, authed_client):
    pool = _mock_pool()
    # member-2 (user-2) is up first (draft_position=1); user-1 trying to pick should be rejected.
    members = [
        {"id": "member-2", "user_id": "user-2", "draft_position": 1, "joined_at": "2026-04-01"},
        {"id": "member-1", "user_id": "user-1", "draft_position": 2, "joined_at": "2026-04-02"},
    ]
    mock_sb.return_value.table.side_effect = _pick_tables_side_effect(
        pool, members, picks=[],
        nba_teams=[{"id": 1, "name": "A", "seed": 1}, {"id": 2, "name": "B", "seed": 2}],
    )

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"nba_team_id": 2, "league": "nba"})
    assert resp.status_code == 403


from routes.draft import _order_members_for_draft


def test_order_members_uses_draft_position_first():
    members = [
        {"id": "a", "joined_at": "2026-04-01T00:00:00Z", "draft_position": None},
        {"id": "b", "joined_at": "2026-04-02T00:00:00Z", "draft_position": 1},
        {"id": "c", "joined_at": "2026-04-03T00:00:00Z", "draft_position": 2},
    ]
    ordered = _order_members_for_draft(members)
    assert [m["id"] for m in ordered] == ["b", "c", "a"]


def test_order_members_falls_back_to_joined_at_when_all_null():
    members = [
        {"id": "a", "joined_at": "2026-04-03T00:00:00Z", "draft_position": None},
        {"id": "b", "joined_at": "2026-04-01T00:00:00Z", "draft_position": None},
        {"id": "c", "joined_at": "2026-04-02T00:00:00Z", "draft_position": None},
    ]
    ordered = _order_members_for_draft(members)
    assert [m["id"] for m in ordered] == ["b", "c", "a"]


def test_order_members_nulls_sorted_by_joined_at_after_positioned():
    members = [
        {"id": "late", "joined_at": "2026-04-05T00:00:00Z", "draft_position": None},
        {"id": "early_null", "joined_at": "2026-04-01T00:00:00Z", "draft_position": None},
        {"id": "pos2", "joined_at": "2026-04-02T00:00:00Z", "draft_position": 2},
        {"id": "pos1", "joined_at": "2026-04-03T00:00:00Z", "draft_position": 1},
    ]
    ordered = _order_members_for_draft(members)
    assert [m["id"] for m in ordered] == ["pos1", "pos2", "early_null", "late"]


@patch("routes.draft.get_service_client")
def test_undo_last_pick_happy_path(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table

    pool = _mock_pool(draft_status="active")
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [pool]
    mock_table.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
        {"id": "pick-3", "pick_order": 3},
        {"id": "pick-2", "pick_order": 2},
        {"id": "pick-1", "pick_order": 1},
    ]
    mock_table.delete.return_value.eq.return_value.execute.return_value.data = [{}]

    resp = authed_client.post("/pool/pool-1/draft/undo")
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "undone_pick_order": 3}
    delete_calls = mock_table.delete.return_value.eq.call_args_list
    assert delete_calls[0][0] == ("id", "pick-3")


@patch("routes.draft.get_service_client")
def test_undo_last_pick_rejects_non_creator(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    pool = _mock_pool(draft_status="active")
    pool["creator_id"] = "someone-else"
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [pool]

    resp = authed_client.post("/pool/pool-1/draft/undo")
    assert resp.status_code == 403


@patch("routes.draft.get_service_client")
def test_undo_last_pick_rejects_inactive_draft(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool(draft_status="pending")]

    resp = authed_client.post("/pool/pool-1/draft/undo")
    assert resp.status_code == 409


@patch("routes.draft.get_service_client")
def test_undo_last_pick_rejects_when_no_picks(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool(draft_status="active")]
    mock_table.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []

    resp = authed_client.post("/pool/pool-1/draft/undo")
    assert resp.status_code == 409


@patch("routes.draft.get_service_client")
def test_assign_pick_happy_path(mock_sb, authed_client):
    pool = _mock_pool(draft_status="active")

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            # .eq(id).eq(pool_id) membership check
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "m1"}]
            # .eq(pool_id) for num_members count
            t.select.return_value.eq.return_value.execute.return_value.data = [{"id": "m1"}, {"id": "m2"}]
        elif name == "nba_teams":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"id": 5}]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
            t.insert.return_value.execute.return_value.data = [{"id": "pick-new"}]
        return t

    mock_sb.return_value.table.side_effect = _side_effect

    resp = authed_client.post("/pool/pool-1/draft/assign", json={
        "member_id": "m1", "team_id": 5, "league": "nba",
    })
    assert resp.status_code == 200


@patch("routes.draft.get_service_client")
def test_assign_pick_rejects_taken_team(mock_sb, authed_client):
    pool = _mock_pool(draft_status="active")

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "m1"}]
        elif name == "nba_teams":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"id": 5}]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
                {"team_id": 5, "nba_team_id": 5, "league": "nba", "pick_order": 1},
            ]
        return t

    mock_sb.return_value.table.side_effect = _side_effect

    resp = authed_client.post("/pool/pool-1/draft/assign", json={
        "member_id": "m1", "team_id": 5, "league": "nba",
    })
    assert resp.status_code == 400


@patch("routes.draft.get_service_client")
def test_remove_pick_happy_path(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool(draft_status="active")]
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "pick-42", "pool_id": "pool-1"}]
    mock_table.delete.return_value.eq.return_value.execute.return_value.data = [{}]

    resp = authed_client.post("/pool/pool-1/draft/remove-pick", json={"pick_id": "pick-42"})
    assert resp.status_code == 200


@patch("routes.scores.recalculate_standings")
@patch("routes.draft.get_service_client")
def test_finalize_draft_happy_path(mock_sb, mock_recalc, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool(draft_status="active")]
    mock_table.update.return_value.eq.return_value.execute.return_value.data = [{}]

    resp = authed_client.post("/pool/pool-1/draft/finalize")
    assert resp.status_code == 200
    mock_table.update.assert_called_with({"draft_status": "complete"})
    mock_recalc.assert_called_once_with("pool-1")


@patch("routes.draft.get_service_client")
def test_finalize_rejects_non_creator(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    pool = _mock_pool(draft_status="active")
    pool["creator_id"] = "someone-else"
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [pool]

    resp = authed_client.post("/pool/pool-1/draft/finalize")
    assert resp.status_code == 403


@patch("routes.draft.get_service_client")
def test_set_draft_order_happy_path(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table

    # Pool lookup (pending, creator is user-1)
    pool = _mock_pool(draft_status="pending")
    # Members fetch returns two members
    def _select_side_effect(*_args, **_kwargs):
        m = MagicMock()
        # chain for .eq(pool_id).execute() -> pool lookup
        m.eq.return_value.execute.return_value.data = [pool]
        # chain for .eq(pool_id).order(joined_at).execute() -> members
        m.eq.return_value.order.return_value.execute.return_value.data = [
            {"id": "m1", "user_id": "user-1"},
            {"id": "m2", "user_id": "user-2"},
        ]
        return m
    mock_table.select.side_effect = _select_side_effect

    # Capture updates
    mock_table.update.return_value.eq.return_value.execute.return_value.data = [{}]

    resp = authed_client.post("/pool/pool-1/draft/order", json={
        "member_ids": ["m2", "m1"],
    })
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True}

    # Two update calls, positions 1 and 2 assigned to m2 and m1 respectively
    update_calls = mock_table.update.call_args_list
    assert len(update_calls) == 2
    assert update_calls[0][0][0] == {"draft_position": 1}
    assert update_calls[1][0][0] == {"draft_position": 2}
