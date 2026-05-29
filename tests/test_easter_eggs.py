import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import datetime
from unittest.mock import patch, MagicMock
from services.easter_eggs import wc_slot, FOOTBALL_QUOTES, KICKOFF_DATE


def _sb_with_games(dates):
    """Mock sb whose game_results query for a competition returns games on the
    given list of game_date strings."""
    rows = [{"game_date": d} for d in dates]
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = rows
    return sb


def test_wc_slot_returns_countdown_before_kickoff():
    sb = _sb_with_games([])
    comp = {"id": "c-wc", "league": "world_cup"}
    today = KICKOFF_DATE - datetime.timedelta(days=5)
    with patch("services.easter_eggs._today", lambda: today):
        slot = wc_slot(sb, comp)
    assert slot["countdown"]["days"] == 5
    assert slot["matchday"] is None
    assert slot["quote"] in FOOTBALL_QUOTES


def test_wc_slot_returns_matchday_during_tournament():
    # Three completed game-dates -> we're on matchday 4 (the next day after the
    # last completed games).
    sb = _sb_with_games(["2026-06-11", "2026-06-12", "2026-06-13"])
    comp = {"id": "c-wc", "league": "world_cup"}
    with patch("services.easter_eggs._today", lambda: datetime.date(2026, 6, 14)):
        slot = wc_slot(sb, comp)
    assert slot["countdown"] is None
    assert slot["matchday"] == 4
    assert slot["quote"] in FOOTBALL_QUOTES


def test_wc_slot_returns_none_for_non_world_cup_competition():
    sb = _sb_with_games([])
    comp = {"id": "c-nba", "league": "nba"}
    assert wc_slot(sb, comp) is None
