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
