import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from unittest.mock import patch, MagicMock
from routes.scores import build_standings_view


@patch("routes.scores.team_color", lambda *a, **k: "#123456")
@patch("routes.scores.get_service_client")
def test_build_standings_view_resolves_roster_via_team_ref(mock_sb):
    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pool_members":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "m1", "user_id": "u1"}]
        elif name == "users":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "u1", "display_name": "Sean"}]
        elif name == "pool_standings":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"member_id": "m1", "total_points": 0}]
        elif name == "game_results":
            t.select.return_value.execute.return_value.data = []
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
                {"member_id": "m1", "team_ref": "t1"}]
        elif name == "teams":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "t1", "competition_id": "c-wc", "ext_id": 202,
                 "name": "Argentina", "abbreviation": "ARG"}]
        return t

    mock_sb.return_value.table.side_effect = _side_effect
    standings, member_teams = build_standings_view("pool-1")
    assert member_teams["m1"][0]["name"] == "Argentina"
    assert member_teams["m1"][0]["wins"] == 0
