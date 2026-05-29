import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

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
