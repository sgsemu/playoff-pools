import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from unittest.mock import patch, MagicMock
from types import SimpleNamespace
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
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "pool-1", "type": "draft",
                 "scoring_config": {"type": "per_win", "points_per_win": 2}}]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "m1", "user_id": "u1"}]
        elif name == "users":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "u1", "display_name": "Sean"}]
        elif name == "pool_standings":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"member_id": "m1", "total_points": 0}]
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = []
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
    assert member_teams["m1"][0]["points"] == 0


@patch("routes.scores.fetch_group_winners", lambda comp: set())
@patch("routes.scores.team_color", lambda *a, **k: "#123456")
@patch("routes.scores.get_service_client")
def test_build_standings_view_shows_stage_points_per_team(mock_sb):
    # Stage-weighted pool: team 203 drew one group game -> 1 pt (not 0 wins).
    def _side_effect(*args, **_kwargs):
        name = args[0] if args else ""
        t = MagicMock()
        if name == "pools":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "pool-1", "type": "draft", "scoring_config": {"type": "stage_weighted"}}]
        elif name == "pool_members":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"id": "m1", "user_id": "u1"}]
        elif name == "users":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "u1", "display_name": "Sean"}]
        elif name == "pool_standings":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"member_id": "m1", "total_points": 1}]
        elif name == "game_results":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"competition_id": "c-wc", "home_team_id": 203, "away_team_id": 467,
                 "home_score": 1, "away_score": 1, "stage": "group", "is_draw": True}]
        elif name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = [
                {"competition_id": "c-wc"}]
        elif name == "competitions":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "c-wc", "espn_sport": "soccer", "espn_slug": "fifa.world",
                 "stages": _WC_STAGES_JSON}]
        elif name == "draft_picks":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
                {"member_id": "m1", "team_ref": "t1"}]
        elif name == "teams":
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"id": "t1", "competition_id": "c-wc", "ext_id": 203,
                 "name": "Mexico", "abbreviation": "MEX"}]
        return t

    mock_sb.return_value.table.side_effect = _side_effect
    standings, member_teams = build_standings_view("pool-1")
    assert member_teams["m1"][0]["wins"] == 0
    assert member_teams["m1"][0]["points"] == 1


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


# ---------------------------------------------------------------------------
# maybe_auto_sync -- task-10 regression: survivor pools have no draft phase
# and must be resolved regardless of draft_status. A plain MagicMock would
# happily return every row through a filtered `.eq("draft_status", ...)`
# chain too (it doesn't actually filter), which would mask the bug -- so
# this fake really applies the filter, the way supabase-py's builder does.
# ---------------------------------------------------------------------------

class _PoolsQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, cols="*"):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _PoolsClient:
    """Minimal supabase double covering only the pools table -- enough to
    prove the real selection predicate used by maybe_auto_sync."""
    def __init__(self, pools):
        self._pools = pools

    def table(self, name):
        assert name == "pools", f"unexpected table {name!r}"
        return _PoolsQuery(self._pools)


@patch("routes.scores.get_service_client")
def test_maybe_auto_sync_resolves_survivor_pool_regardless_of_draft_status(mock_client):
    import routes.scores as scores_mod
    scores_mod._last_auto_sync_at = 0.0  # reset the process-level throttle

    pools = [
        {"id": "surv-pending", "type": "survivor", "draft_status": "pending"},
        {"id": "draft-complete", "type": "draft", "draft_status": "complete"},
        {"id": "draft-pending", "type": "draft", "draft_status": "pending"},
    ]
    mock_client.return_value = _PoolsClient(pools)

    with patch("routes.scores._sync_completed_games", return_value=3), \
         patch("services.survivor_data.resolve_and_apply") as mock_resolve, \
         patch("routes.scores.recalculate_standings") as mock_recalc:
        new_count = scores_mod.maybe_auto_sync(throttle_seconds=0)

    assert new_count == 3
    # Pending survivor pool: resolve_and_apply must run even though its
    # draft_status is still 'pending' (survivor pools have no draft phase).
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[0][1]["id"] == "surv-pending"
    # Complete draft pool: recalculate_standings runs as before.
    mock_recalc.assert_called_once_with("draft-complete")
    # Pending draft pool: neither path fires (no draft, no standings yet).
