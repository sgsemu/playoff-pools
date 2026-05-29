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
    pool = _mock_pool()

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
        elif name == "teams":
            t.select.return_value.in_.return_value.execute.return_value.data = []
            t.select.return_value.eq.return_value.execute.return_value.data = []
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = []
        elif name == "competitions":
            t.select.return_value.in_.return_value.execute.return_value.data = []
        return t

    mock_sb.return_value.table.side_effect = _side_effect

    resp = authed_client.get("/pool/pool-1/draft")
    assert resp.status_code == 200


def _pick_tables_side_effect(pool, members, picks, teams=None, competition_id="c-1"):
    """Per-table mock factory for make_pick tests.

    make_pick touches pools, pool_members (by user_id and ordered by joined_at),
    pool_competitions, teams, draft_picks. Returning the right shape per table is
    easier than threading chained return_values.
    """
    teams = teams or []

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
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"competition_id": competition_id}
            ]
        elif name == "teams":
            # get_team: .select(*).eq("id", ref).execute()
            t.select.return_value.eq.return_value.execute.return_value.data = [
                tm for tm in teams if tm.get("competition_id") == competition_id
            ]
            # get_draftable_teams (via get_pool_competition_ids -> in_): .select(*).in_(...).execute()
            t.select.return_value.in_.return_value.execute.return_value.data = teams
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = picks
            t.insert.return_value.execute.return_value.data = [{"id": "pick-new"}]
        return t

    return _side_effect


@patch("routes.draft.get_service_client")
def test_make_pick(mock_sb, authed_client):
    pool = _mock_pool()
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]
    mock_sb.return_value.table.side_effect = _pick_tables_side_effect(
        pool, members, picks=[],
        teams=[{"id": "t1", "name": "A", "competition_id": "c-1", "league": "nba"}],
    )

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"team_ref": "t1"})
    assert resp.status_code == 200


@patch("routes.draft.get_service_client")
def test_cannot_pick_already_taken_team(mock_sb, authed_client):
    pool = _mock_pool()
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]
    picks = [{"team_ref": "t1", "league": "nba", "member_id": "member-2", "pick_order": 1, "round": 1}]
    mock_sb.return_value.table.side_effect = _pick_tables_side_effect(
        pool, members, picks,
        teams=[{"id": "t1", "name": "A", "competition_id": "c-1", "league": "nba"}],
    )

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"team_ref": "t1"})
    assert resp.status_code == 400


@patch("routes.draft.get_service_client")
def test_make_pick_rejects_inactive_draft(mock_sb, authed_client):
    pool = _mock_pool(draft_status="pending")
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]
    mock_sb.return_value.table.side_effect = _pick_tables_side_effect(pool, members, picks=[])

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"team_ref": "t1"})
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
        teams=[
            {"id": "t1", "name": "A", "competition_id": "c-1", "league": "nba"},
            {"id": "t2", "name": "B", "competition_id": "c-1", "league": "nba"},
        ],
    )

    resp = authed_client.post("/pool/pool-1/draft/pick", json={"team_ref": "t2"})
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
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"competition_id": "c-1"}]
        elif name == "teams":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "t5", "competition_id": "c-1", "name": "Team5", "league": "nba"}
            ]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
            t.insert.return_value.execute.return_value.data = [{"id": "pick-new"}]
        return t

    mock_sb.return_value.table.side_effect = _side_effect

    resp = authed_client.post("/pool/pool-1/draft/assign", json={
        "member_id": "m1", "team_ref": "t5",
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
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"competition_id": "c-1"}]
        elif name == "teams":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "t5", "competition_id": "c-1", "name": "Team5", "league": "nba"}
            ]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
                {"team_ref": "t5", "pick_order": 1},
            ]
        return t

    mock_sb.return_value.table.side_effect = _side_effect

    resp = authed_client.post("/pool/pool-1/draft/assign", json={
        "member_id": "m1", "team_ref": "t5",
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


@patch("routes.draft.get_service_client")
def test_draft_room_groups_teams_by_competition(mock_sb, authed_client):
    pool = _mock_pool(draft_status="active")

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
                {"id": "m1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}
            ]
        elif name == "users":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"display_name": "Test User"}]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"competition_id": "c-wc"}]
        elif name == "competitions":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "c-wc", "league": "world_cup", "name": "FIFA World Cup 2026"}
            ]
        elif name == "teams":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "t1", "competition_id": "c-wc", "ext_id": 202, "name": "Argentina",
                 "abbreviation": "ARG", "grouping": None, "seed": None},
            ]
        return t

    mock_sb.return_value.table.side_effect = _side_effect
    resp = authed_client.get("/pool/pool-1/draft")
    assert resp.status_code == 200


@patch("routes.draft.get_service_client")
def test_make_pick_stores_team_ref(mock_sb, authed_client):
    pool = _mock_pool()
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]
    captured = {}

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [members[0]]
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = members
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"competition_id": "c-wc"}]
        elif name == "teams":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "t1", "competition_id": "c-wc", "name": "Argentina"}
            ]
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "t1", "competition_id": "c-wc", "name": "Argentina"}
            ]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
            def _insert(row):
                captured.update(row)
                r = MagicMock(); r.execute.return_value.data = [{"id": "pick-new"}]; return r
            t.insert.side_effect = _insert
        return t

    mock_sb.return_value.table.side_effect = _side_effect
    resp = authed_client.post("/pool/pool-1/draft/pick", json={"team_ref": "t1"})
    assert resp.status_code == 200
    assert captured["team_ref"] == "t1"
    assert "nba_team_id" not in captured  # legacy int omitted


@patch("routes.draft.get_service_client")
def test_make_pick_rejects_team_outside_pool_competitions(mock_sb, authed_client):
    pool = _mock_pool()
    members = [{"id": "member-1", "user_id": "user-1", "draft_position": 1, "joined_at": "2026-04-01"}]

    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [members[0]]
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = members
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"competition_id": "c-wc"}]
        elif name == "teams":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "t9", "competition_id": "c-OTHER", "name": "Intruder"}
            ]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []
        return t

    mock_sb.return_value.table.side_effect = _side_effect
    resp = authed_client.post("/pool/pool-1/draft/pick", json={"team_ref": "t9"})
    assert resp.status_code == 400


from routes.draft import _get_snake_order


def test_get_snake_order_truncates_to_total_picks_for_non_divisible():
    # 5 members, 48 teams -> ceil(48/5) = 10 rounds, truncated to 48 slots.
    snake = _get_snake_order(["a", "b", "c", "d", "e"], num_rounds=10, total_picks=48)
    assert len(snake) == 48
    assert snake[0] == ("a", 1)               # round 1 forward
    assert snake[4] == ("e", 1)
    assert snake[5] == ("e", 2)               # round 2 reversed
    # Round 10 is reversed; the partial-round truncation keeps the first 3
    # slots of that round, which are members e, d, c.
    assert snake[45] == ("e", 10)
    assert snake[46] == ("d", 10)
    assert snake[47] == ("c", 10)


def test_get_snake_order_no_truncation_returns_full_rounds():
    snake = _get_snake_order(["a", "b"], num_rounds=3)
    assert snake == [("a", 1), ("b", 1), ("b", 2), ("a", 2), ("a", 3), ("b", 3)]
