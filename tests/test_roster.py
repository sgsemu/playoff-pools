# tests/test_roster.py
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


def _make_table_mocks(pool_data, member_data, roster_data, player_data, insert_data=None):
    """Create table-specific mocks so different sb.table() calls return different data."""
    pools_mock = MagicMock()
    pools_mock.select.return_value.eq.return_value.execute.return_value.data = pool_data

    members_mock = MagicMock()
    members_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = member_data

    rosters_mock = MagicMock()
    rosters_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = roster_data
    if insert_data:
        rosters_mock.insert.return_value.execute.return_value.data = insert_data

    players_mock = MagicMock()
    players_mock.select.return_value.eq.return_value.execute.return_value.data = player_data

    def table_router(name):
        return {
            "pools": pools_mock,
            "pool_members": members_mock,
            "salary_rosters": rosters_mock,
            "nba_players": players_mock,
        }.get(name, MagicMock())

    return table_router


@patch("routes.roster.get_service_client")
def test_pick_player(mock_sb, authed_client):
    router = _make_table_mocks(
        pool_data=[{
            "id": "pool-1", "type": "salary_cap", "draft_status": "active",
            "scoring_config": {"type": "salary_cap", "salary_cap": 50000}
        }],
        member_data=[{"id": "member-1"}],
        roster_data=[],  # empty roster
        player_data=[{"id": 101, "name": "Star PG", "position": "PG", "salary_value": 9000}],
        insert_data=[{"id": "roster-1"}],
    )
    mock_sb.return_value.table.side_effect = router

    resp = authed_client.post("/pool/pool-1/roster/pick", json={
        "nba_player_id": 101,
        "position": "PG"
    })
    assert resp.status_code == 200


@patch("routes.roster.get_service_client")
def test_reject_over_salary_cap(mock_sb, authed_client):
    router = _make_table_mocks(
        pool_data=[{
            "id": "pool-1", "type": "salary_cap", "draft_status": "active",
            "scoring_config": {"type": "salary_cap", "salary_cap": 50000}
        }],
        member_data=[{"id": "member-1"}],
        roster_data=[
            {"salary": 12000, "position": "PG"},
            {"salary": 11000, "position": "SG"},
            {"salary": 11000, "position": "SF"},
            {"salary": 11000, "position": "PF"},
        ],
        player_data=[{"id": 105, "salary_value": 9000, "position": "C"}],
    )
    mock_sb.return_value.table.side_effect = router

    resp = authed_client.post("/pool/pool-1/roster/pick", json={
        "nba_player_id": 105,
        "position": "C"
    })
    assert resp.status_code == 400
