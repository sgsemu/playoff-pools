import pytest
from unittest.mock import patch, MagicMock
from services.espn_api import fetch_scoreboard, fetch_game_boxscore, fetch_playoff_teams, fetch_team_roster


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
