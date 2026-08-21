import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from services.sync import sync_competition_results


def _sb_capture(existing_ids, inserts):
    def table(name):
        t = MagicMock()
        if name == "game_results":
            t.select.return_value.eq.return_value.execute.return_value.data = (
                [{"id": "x"}] if False else [])
            def _ins(row):
                inserts.append(row)
                r = MagicMock(); r.execute.return_value.data = [row]; return r
            t.insert.side_effect = _ins
        return t
    sb = MagicMock(); sb.table.side_effect = table
    return sb


@patch("services.sync.fetch_competition_results")
def test_sync_inserts_completed_games_with_competition_and_stage(mock_fetch):
    inserts = []
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 1, "away_score": 0, "is_complete": True, "stage": "group", "is_draw": False},
        {"espn_game_id": "g2", "home_team_id": 1, "away_team_id": 2,
         "home_score": 0, "away_score": 0, "is_complete": False, "stage": "group", "is_draw": False},
    ]
    sb = _sb_capture([], inserts)
    comp = {"id": "c-wc", "league": "world_cup", "espn_sport": "soccer", "espn_slug": "fifa.world", "event_filter": {}}
    n = sync_competition_results(sb, comp)
    assert n == 1                       # only the completed game
    row = inserts[0]
    assert row["competition_id"] == "c-wc"
    assert row["stage"] == "group"
    assert row["is_draw"] is False
    assert row["league"] == "world_cup"   # legacy column still written
    assert row["espn_game_id"] == "g1"


@patch("services.sync.fetch_competition_results")
def test_sync_skips_already_synced_games(mock_fetch):
    inserts = []
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 1, "away_score": 0, "is_complete": True, "stage": "group", "is_draw": False}]
    def table(name):
        t = MagicMock()
        if name == "game_results":
            t.select.return_value.eq.return_value.execute.return_value.data = [{"id": "exists"}]
            t.insert.side_effect = AssertionError("should not insert a duplicate")
        return t
    sb = MagicMock(); sb.table.side_effect = table
    comp = {"id": "c-wc", "league": "world_cup", "espn_sport": "soccer", "espn_slug": "fifa.world", "event_filter": {}}
    assert sync_competition_results(sb, comp) == 0


# ---------------------------------------------------------------------------
# Vercel cron entrypoint (api/cron/sync_games.py) -- task-10 regression: the
# post-sync pool loop used to filter ALL pools by draft_status=='complete'
# before branching on type, which made survivor auto-resolution unreachable
# in production (survivor pools default to draft_status='pending' and only
# flip to 'complete' via settle_season at season end). A plain MagicMock
# wouldn't catch this -- it returns the same data through a filtered
# `.eq(...)` chain as through an unfiltered one -- so this fake really
# applies the filter, the way supabase-py's builder does.
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
    """Minimal supabase double covering only the pools table."""
    def __init__(self, pools):
        self._pools = pools

    def table(self, name):
        assert name == "pools", f"unexpected table {name!r}"
        return _PoolsQuery(self._pools)


def test_sync_games_cron_resolves_survivor_pool_regardless_of_draft_status():
    from api.cron import sync_games as cron_mod

    pools = [
        {"id": "surv-pending", "type": "survivor", "draft_status": "pending"},
        {"id": "draft-complete", "type": "draft", "draft_status": "complete"},
        {"id": "draft-pending", "type": "draft", "draft_status": "pending"},
    ]

    with patch.object(cron_mod, "get_service_client", return_value=_PoolsClient(pools)), \
         patch.object(cron_mod, "competitions_for_active_pools", return_value=[{"id": "c1"}]), \
         patch.object(cron_mod, "sync_competition_results", return_value=2), \
         patch.object(cron_mod, "resolve_and_apply") as mock_resolve, \
         patch.object(cron_mod, "recalculate_standings") as mock_recalc:
        client = cron_mod.app.test_client()
        resp = client.get("/api/cron/sync-games")

    assert resp.get_json() == {"synced": 2}
    # Pending survivor pool: resolve_and_apply must run even though its
    # draft_status is still 'pending' (survivor pools have no draft phase).
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[0][1]["id"] == "surv-pending"
    # Complete draft pool: recalculate_standings runs as before.
    mock_recalc.assert_called_once_with("draft-complete")
    # Pending draft pool: neither path fires (no draft, no standings yet).
