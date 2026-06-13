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


def _standings_entry(team_id, rank, games_played):
    return {"team": {"id": team_id}, "stats": [
        {"name": "rank", "value": float(rank)},
        {"name": "gamesPlayed", "value": float(games_played)},
    ]}


def test_fetch_group_winners_empty_before_group_stage_complete():
    # Tournament not started (or mid-group-stage): ESPN still ranks one team #1
    # per group even at gamesPlayed=0. No group is decided yet, so no winners —
    # otherwise the +2 group-winner bonus is awarded on day zero.
    payload = {"children": [
        {"name": "Group A", "standings": {"entries": [
            _standings_entry("203", 1, 0), _standings_entry("467", 2, 0),
            _standings_entry("560", 3, 0), _standings_entry("561", 4, 0)]}},
    ]}
    from services.espn_api import fetch_group_winners
    with patch("services.espn_api.requests.get") as g:
        g.return_value = MagicMock(json=lambda: payload)
        g.return_value.raise_for_status = lambda: None
        winners = fetch_group_winners({"espn_sport": "soccer", "espn_slug": "fifa.world"})
    assert winners == set()


def test_fetch_group_winners_returns_rank1_once_group_complete():
    # Group stage finished: 4-team round-robin, each team played 3. Rank-1 wins.
    payload = {"children": [
        {"name": "Group A", "standings": {"entries": [
            _standings_entry("203", 1, 3), _standings_entry("467", 2, 3),
            _standings_entry("560", 3, 3), _standings_entry("561", 4, 3)]}},
        {"name": "Group B", "standings": {"entries": [
            _standings_entry("202", 1, 3), _standings_entry("212", 2, 3),
            _standings_entry("213", 3, 3), _standings_entry("214", 4, 3)]}},
    ]}
    from services.espn_api import fetch_group_winners
    with patch("services.espn_api.requests.get") as g:
        g.return_value = MagicMock(json=lambda: payload)
        g.return_value.raise_for_status = lambda: None
        winners = fetch_group_winners({"espn_sport": "soccer", "espn_slug": "fifa.world"})
    assert winners == {203, 202}


def _finals_event(headline, summary, wins, completed):
    return {"competitions": [{
        "notes": [{"headline": headline}],
        "series": {"type": "playoff", "summary": summary, "completed": completed,
                   "competitors": [{"id": "1", "wins": wins[0]}, {"id": "2", "wins": wins[1]}]},
    }]}


def _finals_payload(*events):
    return {"events": list(events)}


def _call_finals(payload):
    from services.espn_api import fetch_finals_complete
    with patch("services.espn_api.requests.get") as g:
        g.return_value = MagicMock(json=lambda: payload, raise_for_status=lambda: None)
        return fetch_finals_complete({"espn_sport": "basketball", "espn_slug": "nba"})


def test_fetch_finals_complete_false_when_series_in_progress():
    assert _call_finals(_finals_payload(
        _finals_event("NBA Finals - Game 5", "NY leads series 3-1", (3, 1), False))) is False


def test_fetch_finals_complete_true_when_team_reaches_four():
    assert _call_finals(_finals_payload(
        _finals_event("NBA Finals - Game 6", "NY wins series 4-2", (4, 2), True))) is True


def test_fetch_finals_complete_true_on_wins_even_if_completed_flag_missing():
    ev = _finals_event("Stanley Cup Final - Game 7", "CAR wins 4-3", (4, 3), None)
    ev["competitions"][0]["series"].pop("completed")
    assert _call_finals(_finals_payload(ev)) is True


def test_fetch_finals_complete_ignores_completed_conference_finals():
    # A completed Conference Finals series must NOT count as the championship.
    assert _call_finals(_finals_payload(
        _finals_event("Western Conference Finals - Game 6", "NY wins 4-2", (4, 2), True))) is False


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
