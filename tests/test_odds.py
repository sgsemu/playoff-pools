import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from services.odds import sport_key, best_by_outcome, best_spread_by_team


# ---------------------------------------------------------------------------
# sport_key
# ---------------------------------------------------------------------------

def test_sport_key_nfl():
    assert sport_key("nfl") == "americanfootball_nfl"


def test_sport_key_existing_leagues_unaffected():
    assert sport_key("nba") == "basketball_nba"
    assert sport_key("nhl") == "icehockey_nhl"
    assert sport_key("world_cup") == "soccer_fifa_world_cup"


def test_sport_key_unknown_league_returns_none():
    assert sport_key("mlb") is None


# ---------------------------------------------------------------------------
# best_spread_by_team
# ---------------------------------------------------------------------------

def _event_with_spreads():
    return {
        "home_team": "Kansas City Chiefs",
        "away_team": "Buffalo Bills",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -150},
                            {"name": "Buffalo Bills", "price": 130},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -110, "point": -3.5},
                            {"name": "Buffalo Bills", "price": -110, "point": 3.5},
                        ],
                    },
                ],
            },
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -110, "point": -3},
                            {"name": "Buffalo Bills", "price": -110, "point": 3},
                        ],
                    },
                ],
            },
        ],
    }


def test_best_spread_by_team_favorite_negative_underdog_positive():
    result = best_spread_by_team(_event_with_spreads())
    assert result["Kansas City Chiefs"] < 0
    assert result["Buffalo Bills"] > 0


def test_best_spread_by_team_keys_by_team_name():
    result = best_spread_by_team(_event_with_spreads())
    assert set(result.keys()) == {"Kansas City Chiefs", "Buffalo Bills"}


def test_best_spread_by_team_ignores_h2h_market():
    # Only one bookmaker (draftkings) has both h2h and spreads; the h2h
    # market's price/point-less outcomes must not leak into the result.
    result = best_spread_by_team(_event_with_spreads())
    assert result["Kansas City Chiefs"] in (-3.5, -3)


def test_best_spread_by_team_no_spreads_market_returns_empty():
    event = {
        "home_team": "Kansas City Chiefs",
        "away_team": "Buffalo Bills",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -150},
                            {"name": "Buffalo Bills", "price": 130},
                        ],
                    },
                ],
            }
        ],
    }
    assert best_spread_by_team(event) == {}


def test_best_by_outcome_unaffected_by_spreads_market_present():
    # Sanity check that best_by_outcome still only reads h2h even when a
    # spreads market is present on the same bookmaker.
    result = best_by_outcome(_event_with_spreads())
    assert result["Kansas City Chiefs"]["price"] == -150
    assert result["Buffalo Bills"]["price"] == 130
