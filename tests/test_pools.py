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


from routes.pools import get_addable_players


def test_get_addable_players_includes_co_players_excludes_members_and_self():
    # Commissioner u1 is in pools p1 (target) and p2 (prior). Co-members across
    # those pools are {u1, u2, u3}. p1 already has {u1, u3}. Addable -> {u2}.
    def table(name):
        t = MagicMock()
        if name == "pool_members":
            def _select(col):
                s = MagicMock()
                if col == "pool_id":
                    s.eq.return_value.execute.return_value.data = [
                        {"pool_id": "p1"}, {"pool_id": "p2"}]
                elif col == "user_id":
                    s.in_.return_value.execute.return_value.data = [
                        {"user_id": "u1"}, {"user_id": "u2"}, {"user_id": "u3"}]
                    s.eq.return_value.execute.return_value.data = [
                        {"user_id": "u1"}, {"user_id": "u3"}]
                return s
            t.select.side_effect = _select
        elif name == "users":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "u2", "display_name": "Mike"}]
        return t

    sb = MagicMock()
    sb.table.side_effect = table
    result = get_addable_players(sb, "p1", "u1")
    assert [u["id"] for u in result] == ["u2"]


def _pool_only(creator_id, draft_status):
    def table(name):
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "pool-1", "creator_id": creator_id, "draft_status": draft_status}]
        return t
    return table


@patch("routes.pools.get_addable_players")
@patch("routes.pools.get_service_client")
def test_add_member_happy_path(mock_sb, mock_addable, authed_client):
    mock_addable.return_value = [{"id": "u2", "display_name": "Mike"}]
    inserted = {}

    def table(name):
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "pool-1", "creator_id": "test-uuid", "draft_status": "pending"}]
        elif name == "pool_members":
            def _ins(row):
                inserted.update(row)
                r = MagicMock(); r.execute.return_value.data = [{}]; return r
            t.insert.side_effect = _ins
        return t

    mock_sb.return_value.table.side_effect = table
    resp = authed_client.post("/pool/pool-1/members/add", data={"user_id": "u2"})
    assert resp.status_code == 302
    assert inserted == {"pool_id": "pool-1", "user_id": "u2", "role": "member"}


@patch("routes.pools.get_addable_players")
@patch("routes.pools.get_service_client")
def test_add_member_rejects_non_creator(mock_sb, mock_addable, authed_client):
    mock_sb.return_value.table.side_effect = _pool_only("someone-else", "pending")
    resp = authed_client.post("/pool/pool-1/members/add", data={"user_id": "u2"})
    assert resp.status_code == 403


@patch("routes.pools.get_addable_players")
@patch("routes.pools.get_service_client")
def test_add_member_rejects_after_draft_starts(mock_sb, mock_addable, authed_client):
    mock_sb.return_value.table.side_effect = _pool_only("test-uuid", "active")
    resp = authed_client.post("/pool/pool-1/members/add", data={"user_id": "u2"})
    assert resp.status_code == 409


@patch("routes.pools.get_addable_players")
@patch("routes.pools.get_service_client")
def test_add_member_rejects_user_outside_circle(mock_sb, mock_addable, authed_client):
    mock_addable.return_value = [{"id": "u2", "display_name": "Mike"}]
    mock_sb.return_value.table.side_effect = _pool_only("test-uuid", "pending")
    resp = authed_client.post("/pool/pool-1/members/add", data={"user_id": "u9"})
    assert resp.status_code == 403


@patch("routes.pools.get_service_client")
def test_join_pool_rejects_when_draft_started(mock_sb, authed_client):
    def table(name):
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "pool-1", "name": "Active Pool", "draft_status": "active",
                 "invite_code": "ABC123"}
            ]
        elif name == "pool_members":
            # user is not yet a member
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            # if insert is called, the test fails loudly
            t.insert.side_effect = AssertionError("must not insert into a started pool")
        return t

    mock_sb.return_value.table.side_effect = table
    resp = authed_client.get("/join/ABC123", follow_redirects=False)
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers.get("Location", "")
