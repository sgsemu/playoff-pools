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


@patch("routes.draft.get_service_client")
def test_make_pick(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    # Pool lookup
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool()]
    # Current member
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "member-1", "user_id": "user-1"}
    ]
    # Existing picks (none)
    mock_table.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
    # Insert pick
    mock_table.insert.return_value.execute.return_value.data = [{"id": "pick-1"}]

    resp = authed_client.post("/pool/pool-1/draft/pick", json={
        "nba_team_id": 1
    })
    assert resp.status_code == 200


@patch("routes.draft.get_service_client")
def test_cannot_pick_already_taken_team(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool()]
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "member-1", "user_id": "user-1"}
    ]
    # Team 1 already picked
    mock_table.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
        {"nba_team_id": 1, "member_id": "member-2", "pick_order": 1, "round": 1}
    ]

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"nba_team_id": 1})
    assert resp.status_code == 400


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
