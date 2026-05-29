import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from unittest.mock import patch, MagicMock
from routes.scores import build_standings_view

_WC_STAGES_JSON = [
    {"key": "group", "win_points": 3, "draw_points": 1, "group_winner_bonus": 2},
    {"key": "r32", "win_points": 3},
    {"key": "r16", "win_points": 3},
    {"key": "qf", "win_points": 3},
    {"key": "sf", "win_points": 4},
    {"key": "final", "win_points": 5},
    {"key": "third_place", "win_points": 3},
]


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


@patch("routes.scores.fetch_group_winners", lambda comp: {203})
@patch("routes.scores.get_service_client")
def test_recalculate_stage_weighted_pool(mock_sb):
    pool = {"id": "p1", "type": "draft", "scoring_config": {"type": "stage_weighted"}}
    def table(name):
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [pool]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"id": "m1", "user_id": "u1"}]
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"competition_id": "c-wc"}]
        elif name == "competitions":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "c-wc", "league": "world_cup", "espn_sport": "soccer", "espn_slug": "fifa.world",
                 "stages": _WC_STAGES_JSON}]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"member_id": "m1", "team_ref": "t1"}]
        elif name == "teams":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "t1", "competition_id": "c-wc", "ext_id": 203}]
        elif name == "game_results":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"competition_id": "c-wc", "home_team_id": 203, "away_team_id": 467,
                 "home_score": 1, "away_score": 0, "stage": "group", "is_draw": False}]
        elif name == "pool_standings":
            t.upsert.return_value.execute.return_value.data = [{}]
        elif name == "users":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"display_name": "Sean"}]
        return t
    mock_sb.return_value.table.side_effect = table
    from routes.scores import recalculate_standings
    recalculate_standings("p1")   # 203 won a group match (3) + group winner (2) = 5
