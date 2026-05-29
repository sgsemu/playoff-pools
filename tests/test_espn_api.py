import json, os
import pytest
from unittest.mock import patch, MagicMock
from services.espn_api import fetch_scoreboard, fetch_game_boxscore, fetch_playoff_teams, fetch_team_roster, fetch_calendar_games, fetch_live_games

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "fifa_world_group_scoreboard.json")


@patch("services.espn_api.requests.get")
def test_fetch_scoreboard(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "events": [
            {
                "id": "401234567",
                "competitions": [{
                    "competitors": [
                        {"team": {"id": "1"}, "homeAway": "home", "score": "105"},
                        {"team": {"id": "2"}, "homeAway": "away", "score": "98"}
                    ],
                    "status": {"type": {"completed": True}}
                }],
                "season": {"slug": "post-season"}
            }
        ]
    }
    games = fetch_scoreboard()
    assert len(games) == 1
    assert games[0]["espn_game_id"] == "401234567"
    assert games[0]["home_score"] == 105
    assert games[0]["away_score"] == 98


@patch("services.espn_api.requests.get")
def test_fetch_game_boxscore(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "boxscore": {
            "players": [
                {
                    "team": {"id": "1"},
                    "statistics": [{
                        "athletes": [{
                            "athlete": {"id": "101", "displayName": "Player A"},
                            "stats": ["32", "8", "5", "2", "1", "3", "36"]
                        }]
                    }]
                }
            ]
        }
    }
    players = fetch_game_boxscore("401234567")
    assert len(players) == 1
    assert players[0]["name"] == "Player A"
    assert players[0]["points"] == 32


@patch("services.espn_api.requests.get")
def test_fetch_playoff_teams(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "sports": [{"leagues": [{"teams": [
            {"team": {"id": "1", "displayName": "Boston Celtics", "abbreviation": "BOS",
                      "groups": {"id": "4"}, "record": {"items": [{"summary": "60-22"}]}}}
        ]}]}]
    }
    teams = fetch_playoff_teams()
    assert len(teams) >= 1
    assert teams[0]["name"] == "Boston Celtics"


def test_resolve_stage_maps_group_slug():
    from services.espn_api import resolve_stage
    assert resolve_stage("world_cup", "group-stage") == "group"
    assert resolve_stage("world_cup", "round-of-16") == "r16"
    assert resolve_stage("world_cup", "totally-unknown") is None


def test_fetch_competition_results_parses_group_fixture():
    from services.espn_api import resolve_stage, fetch_competition_results
    with open(_FIXTURE) as f:
        payload = json.load(f)
    comp = {"league": "world_cup", "espn_sport": "soccer", "espn_slug": "fifa.world",
            "event_filter": {"all_tournament": True}}
    with patch("services.espn_api.requests.get") as g:
        g.return_value = MagicMock(status_code=200, json=lambda: payload)
        g.return_value.raise_for_status = lambda: None
        games = fetch_competition_results(comp, dates="20260611")
    assert isinstance(games, list)
    # Every parsed game has the fields the sync needs.
    for g_ in games:
        assert set(g_) >= {"espn_game_id", "home_team_id", "away_team_id",
                           "home_score", "away_score", "is_complete", "stage", "is_draw"}
        assert g_["stage"] == "group"


def test_fetch_competition_results_detects_draw():
    from services.espn_api import fetch_competition_results
    payload = {"events": [{
        "id": "1", "season": {"slug": "group-stage"},
        "competitions": [{"status": {"type": {"completed": True}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "203"}, "score": "1", "winner": False},
                {"homeAway": "away", "team": {"id": "467"}, "score": "1", "winner": False}]}]}]}
    comp = {"league": "world_cup", "espn_sport": "soccer", "espn_slug": "fifa.world", "event_filter": {"all_tournament": True}}
    with patch("services.espn_api.requests.get") as g:
        g.return_value = MagicMock(json=lambda: payload)
        g.return_value.raise_for_status = lambda: None
        games = fetch_competition_results(comp, dates="20260611")
    assert games[0]["is_draw"] is True


def test_fetch_group_winners_returns_rank1_per_group():
    payload = {"children": [
        {"name": "Group A", "standings": {"entries": [
            {"team": {"id": "203"}, "stats": [{"name": "rank", "value": 1}]},
            {"team": {"id": "467"}, "stats": [{"name": "rank", "value": 2}]}]}},
        {"name": "Group B", "standings": {"entries": [
            {"team": {"id": "202"}, "stats": [{"name": "rank", "value": 1}]}]}},
    ]}
    from services.espn_api import fetch_group_winners
    with patch("services.espn_api.requests.get") as g:
        g.return_value = MagicMock(json=lambda: payload)
        g.return_value.raise_for_status = lambda: None
        winners = fetch_group_winners({"espn_sport": "soccer", "espn_slug": "fifa.world"})
    assert winners == {203, 202}


def test_fetch_calendar_games_iterates_competitions_and_tags_league():
    payloads = {
        ("basketball", "nba"): {"events": []},
        ("soccer", "fifa.world"): {"events": [{
            "id": "g1", "season": {"slug": "group-stage"},
            "competitions": [{
                "status": {"type": {"state": "post", "completed": True, "shortDetail": "FT"}},
                "competitors": [
                    {"homeAway": "home", "team": {"id": "203", "abbreviation": "MEX",
                                                   "displayName": "Mexico"}, "score": "1", "winner": False},
                    {"homeAway": "away", "team": {"id": "467", "abbreviation": "RSA",
                                                   "displayName": "South Africa"}, "score": "1", "winner": False},
                ],
            }],
        }]},
    }

    def fake_get(url, params=None, **_kw):
        # URL embeds /sports/<sport>/<slug>/scoreboard
        for (sport, slug), body in payloads.items():
            if f"/sports/{sport}/{slug}/scoreboard" in url:
                r = MagicMock(); r.json = lambda body=body: body; r.raise_for_status = lambda: None
                return r
        r = MagicMock(); r.json = lambda: {"events": []}; r.raise_for_status = lambda: None
        return r

    competitions = [
        {"league": "nba", "espn_sport": "basketball", "espn_slug": "nba",
         "event_filter": {"season_type": 3}},
        {"league": "world_cup", "espn_sport": "soccer", "espn_slug": "fifa.world",
         "event_filter": {"all_tournament": True}},
    ]
    with patch("services.espn_api.requests.get", side_effect=fake_get):
        by_date = fetch_calendar_games(competitions, days_back=0, days_forward=0)

    # Soccer match landed in the calendar, tagged league=world_cup, is_draw=True.
    days = list(by_date.values())
    assert any(any(g["league"] == "world_cup" and g["is_draw"] for g in d["games"]) for d in days)


def test_fetch_live_games_filters_in_progress_and_tags_league():
    payload = {"events": [{
        "id": "g1", "season": {"slug": "group-stage"},
        "competitions": [{
            "status": {"type": {"state": "in", "completed": False, "shortDetail": "65'"}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "203", "abbreviation": "MEX",
                                               "displayName": "Mexico"}, "score": "1", "winner": False},
                {"homeAway": "away", "team": {"id": "467", "abbreviation": "RSA",
                                               "displayName": "South Africa"}, "score": "0", "winner": False},
            ],
        }],
    }]}
    competitions = [{"league": "world_cup", "espn_sport": "soccer", "espn_slug": "fifa.world",
                     "event_filter": {"all_tournament": True}}]
    with patch("services.espn_api.requests.get") as g:
        g.return_value = MagicMock(json=lambda: payload, raise_for_status=lambda: None)
        live = fetch_live_games(competitions)
    assert len(live) == 1 and live[0]["league"] == "world_cup"
