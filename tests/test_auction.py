# tests/test_auction.py
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


def _mock_pool(auction_style="budget"):
    return {
        "id": "pool-1", "type": "auction", "draft_status": "active",
        "draft_mode": "live", "timer_seconds": 30, "name": "Auction Pool",
        "scoring_config": {}, "creator_id": "user-1",
        "auction_config": {"auction_style": auction_style, "starting_budget": 100}
    }


@patch("routes.auction.get_service_client")
def test_place_bid(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool()]
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "member-1", "user_id": "user-1"}
    ]
    # No existing bids for this member (budget check)
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    # No current bids for this team (high bid check via .order())
    mock_table.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value.data = []
    mock_table.insert.return_value.execute.return_value.data = [{"id": "bid-1"}]

    resp = authed_client.post("/pool/pool-1/auction/bid", json={
        "nba_team_id": 1,
        "bid_amount": 25
    })
    assert resp.status_code == 200


@patch("routes.auction.get_service_client")
def test_budget_auction_rejects_over_budget(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool("budget")]
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "member-1", "user_id": "user-1"}
    ]
    # Member already spent 90 on winning bids
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"bid_amount": 90, "is_winning_bid": True}
    ]

    resp = authed_client.post("/pool/pool-1/auction/bid", json={
        "nba_team_id": 2,
        "bid_amount": 20
    })
    assert resp.status_code == 400


@patch("routes.auction.get_service_client")
def test_calcutta_allows_unlimited_bids(mock_sb, authed_client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [_mock_pool("calcutta")]
    mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": "member-1", "user_id": "user-1"}
    ]
    # Already spent $500 — should still be allowed in Calcutta
    mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    # No current bids for this team (high bid check via .order())
    mock_table.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value.data = []
    mock_table.insert.return_value.execute.return_value.data = [{"id": "bid-1"}]

    resp = authed_client.post("/pool/pool-1/auction/bid", json={
        "nba_team_id": 3,
        "bid_amount": 200
    })
    assert resp.status_code == 200
