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
        sess["user_id"] = "test-uuid"
        sess["display_name"] = "Test User"
    return client


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_dashboard_requires_login(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


@patch("routes.pools.get_service_client")
def test_dashboard_loads(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = []

    resp = authed_client.get("/dashboard")
    assert resp.status_code == 200
    assert b"My Pools" in resp.data


@patch("routes.pools.get_service_client")
def test_create_pool(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.insert.return_value.execute.return_value.data = [
        {"id": "pool-uuid", "invite_code": "ABC123"}
    ]

    resp = authed_client.post("/pool/create", data={
        "name": "Friends Pool",
        "type": "draft",
        "draft_mode": "live",
        "buy_in": "$50",
        "payout_description": "70/30",
        "scoring_type": "per_win",
        "points_per_win": "1",
        "timer_seconds": "60"
    }, follow_redirects=False)
    assert resp.status_code == 302


@patch("routes.pools.get_service_client")
def test_create_pool_writes_pool_competitions(mock_sb, authed_client):
    captured = {"pool_competitions": []}

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "competitions":
            t.select.return_value.in_.return_value.execute.return_value.data = []
        elif name == "pools":
            t.insert.return_value.execute.return_value.data = [{"id": "pool-1"}]
        elif name == "pool_members":
            t.insert.return_value.execute.return_value.data = [{}]
        elif name == "pool_competitions":
            def _ins(rows):
                captured["pool_competitions"] = rows
                r = MagicMock(); r.execute.return_value.data = rows; return r
            t.insert.side_effect = _ins
        return t

    mock_sb.return_value.table.side_effect = _side_effect
    resp = authed_client.post("/pool/create", data={
        "name": "WC Pool", "type": "draft", "scoring_type": "combo",
        "competition_ids": "c-wc",
    })
    assert resp.status_code in (302, 303)
    assert captured["pool_competitions"] == [{"pool_id": "pool-1", "competition_id": "c-wc"}]


@patch("routes.pools.get_service_client")
def test_create_pool_inherits_stage_weighted_scoring(mock_sb, authed_client):
    captured = {}

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "competitions":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"scoring_defaults": {"type": "stage_weighted"}}
            ]
        elif name == "pools":
            def _ins(row):
                captured.update(row)
                r = MagicMock(); r.execute.return_value.data = [{"id": "pool-1"}]; return r
            t.insert.side_effect = _ins
        elif name == "pool_members":
            t.insert.return_value.execute.return_value.data = [{}]
        elif name == "pool_competitions":
            t.insert.return_value.execute.return_value.data = [{}]
        return t

    mock_sb.return_value.table.side_effect = _side_effect
    resp = authed_client.post("/pool/create", data={
        "name": "WC Pool", "type": "draft", "scoring_type": "combo",
        "competition_ids": "c-wc",
    })
    assert resp.status_code in (302, 303)
    assert captured["scoring_config"] == {"type": "stage_weighted"}


@patch("routes.pools.get_service_client")
def test_join_pool_via_invite(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    # Pool lookup
    mock_sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "pool-uuid", "name": "Friends Pool"}
    ]
    # Member check (not yet a member)
    mock_sb.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    mock_table.insert.return_value.execute.return_value.data = [{"id": "member-uuid"}]

    resp = authed_client.get("/join/ABC123", follow_redirects=False)
    assert resp.status_code == 302
