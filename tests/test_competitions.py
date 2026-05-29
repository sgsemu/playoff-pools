import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from unittest.mock import MagicMock
from services.competitions import get_pool_competition_ids, get_draftable_teams


def _sb_with(pool_competitions, teams_by_competition):
    """Mock sb where:
       pool_competitions -> rows for the .eq(pool_id) query
       teams_by_competition -> {competition_id: [team rows]} for the .in_ query
    """
    def table(name):
        t = MagicMock()
        if name == "pool_competitions":
            t.select.return_value.eq.return_value.execute.return_value.data = pool_competitions
        elif name == "teams":
            # flatten every team whose competition_id is requested
            all_teams = [tm for rows in teams_by_competition.values() for tm in rows]
            t.select.return_value.in_.return_value.execute.return_value.data = all_teams
        return t
    sb = MagicMock()
    sb.table.side_effect = table
    return sb


def test_get_pool_competition_ids_returns_all_linked():
    sb = _sb_with(
        pool_competitions=[{"competition_id": "c-nba"}, {"competition_id": "c-nhl"}],
        teams_by_competition={},
    )
    assert sorted(get_pool_competition_ids(sb, "pool-1")) == ["c-nba", "c-nhl"]


def test_get_pool_competition_ids_empty_when_none_linked():
    sb = _sb_with(pool_competitions=[], teams_by_competition={})
    assert get_pool_competition_ids(sb, "pool-1") == []


def test_get_draftable_teams_returns_teams_for_pool_competitions():
    sb = _sb_with(
        pool_competitions=[{"competition_id": "c-wc"}],
        teams_by_competition={"c-wc": [
            {"id": "t1", "competition_id": "c-wc", "ext_id": 202, "name": "Argentina",
             "abbreviation": "ARG", "grouping": None, "seed": None},
        ]},
    )
    teams = get_draftable_teams(sb, "pool-1")
    assert [t["name"] for t in teams] == ["Argentina"]
    assert teams[0]["id"] == "t1"


def test_get_draftable_teams_empty_when_no_competitions():
    sb = _sb_with(pool_competitions=[], teams_by_competition={})
    assert get_draftable_teams(sb, "pool-1") == []
