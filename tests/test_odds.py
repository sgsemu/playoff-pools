import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from unittest.mock import MagicMock, patch

import pytest

import services.odds as odds_mod
from services.odds import (
    sport_key,
    best_by_outcome,
    best_spread_by_team,
    best_total,
    fetch_odds,
    refresh_odds_lines,
    refresh_odds_props,
    featured_prop,
    can_refresh,
    credits_remaining,
)


@pytest.fixture(autouse=True)
def _clear_memo():
    """Every test gets a clean per-process memo so cache reads/writes in one
    test can't leak into the next."""
    odds_mod._MEMO.clear()
    yield
    odds_mod._MEMO.clear()


def _fake_supabase_client(rows_by_key=None):
    """Build a MagicMock mimicking the supabase-py fluent client enough for
    _cache_get/_cache_put: client.table(x).select(...).eq(...).limit(...).execute()
    and client.table(x).upsert(...).execute(). `rows_by_key` maps cache_key ->
    payload for the rows the fake "table" should return on select."""
    rows_by_key = rows_by_key or {}
    client = MagicMock()
    captured_upserts = []

    def table(name):
        tbl = MagicMock()

        def select(*_a, **_kw):
            sel = MagicMock()

            def eq(_col, value):
                eqm = MagicMock()

                def limit(_n):
                    limm = MagicMock()
                    resp = MagicMock()
                    payload = rows_by_key.get(value)
                    resp.data = [{"payload": payload}] if payload is not None else []
                    limm.execute.return_value = resp
                    return limm

                eqm.limit = limit
                return eqm

            sel.eq = eq
            return sel

        def upsert(record, **_kw):
            captured_upserts.append(record)
            upm = MagicMock()
            upm.execute.return_value = MagicMock()
            return upm

        tbl.select = select
        tbl.upsert = upsert
        return tbl

    client.table = table
    client._captured_upserts = captured_upserts
    return client


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


# ---------------------------------------------------------------------------
# best_total
# ---------------------------------------------------------------------------

def _event_with_totals():
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
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 47.5},
                            {"name": "Under", "price": -105, "point": 47.5},
                        ],
                    },
                ],
            },
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -105, "point": 47.5},
                            {"name": "Under", "price": -110, "point": 47.5},
                        ],
                    },
                ],
            },
        ],
    }


def test_best_total_parses_totals_market():
    result = best_total(_event_with_totals())
    assert result["point"] == 47.5
    # Best (highest decimal payout) price picked per side across books.
    assert result["over"]["price"] == -105
    assert result["over"]["book_name"] == "FanDuel"
    assert result["under"]["price"] == -105
    assert result["under"]["book_name"] == "DraftKings"


def test_best_total_ignores_h2h_market():
    # h2h's price/point-less outcomes on the same bookmaker must not leak in.
    result = best_total(_event_with_totals())
    assert result["point"] == 47.5


def test_best_total_no_totals_market_returns_none():
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
    assert best_total(event) is None


def test_best_total_no_bookmakers_returns_none():
    assert best_total({"home_team": "A", "away_team": "B", "bookmakers": []}) is None


# ---------------------------------------------------------------------------
# fetch_odds -- cache-ONLY, must never call The Odds API
# ---------------------------------------------------------------------------

def test_fetch_odds_returns_cached_payload_without_calling_api():
    fake_events = [{"home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills"}]
    fake_client = _fake_supabase_client({"oddsapi:americanfootball_nfl": fake_events})

    def _blow_up(*_a, **_kw):
        raise AssertionError("fetch_odds must never call requests.get")

    with patch("services.odds.get_service_client", return_value=fake_client):
        with patch("services.odds.requests.get", side_effect=_blow_up) as mock_get:
            result = fetch_odds("nfl")

    assert result == fake_events
    mock_get.assert_not_called()


def test_fetch_odds_returns_empty_list_when_cache_cold():
    fake_client = _fake_supabase_client({})  # no rows anywhere
    with patch("services.odds.get_service_client", return_value=fake_client):
        with patch("services.odds.requests.get") as mock_get:
            result = fetch_odds("nfl")
    assert result == []
    mock_get.assert_not_called()


def test_fetch_odds_unknown_league_returns_empty_list_without_touching_supabase():
    with patch("services.odds.get_service_client") as mock_client:
        result = fetch_odds("mlb")
    assert result == []
    mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# refresh_odds_lines -- the only function allowed to call The Odds API
# ---------------------------------------------------------------------------

def test_refresh_odds_lines_calls_api_once_and_writes_cache():
    fake_data = [{"home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills"}]
    fake_resp = MagicMock()
    fake_resp.json.return_value = fake_data
    fake_resp.raise_for_status.return_value = None
    fake_resp.headers = {"x-requests-remaining": "487", "x-requests-used": "13"}
    fake_client = _fake_supabase_client({})

    with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-key"}):
        with patch("services.odds.get_service_client", return_value=fake_client):
            with patch("services.odds.requests.get", return_value=fake_resp) as mock_get:
                result = refresh_odds_lines("nfl")

    assert result == fake_data
    mock_get.assert_called_once()
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["markets"] == "h2h,spreads,totals"

    upserted = fake_client._captured_upserts
    keys = {row["cache_key"] for row in upserted}
    assert "oddsapi:americanfootball_nfl" in keys
    assert "oddsapi:_meta" in keys
    events_row = next(r for r in upserted if r["cache_key"] == "oddsapi:americanfootball_nfl")
    assert events_row["payload"] == fake_data
    meta_row = next(r for r in upserted if r["cache_key"] == "oddsapi:_meta")
    assert meta_row["payload"]["remaining"] == 487
    assert meta_row["payload"]["used"] == 13


def test_refresh_odds_lines_returns_none_on_request_failure_and_never_raises():
    fake_client = _fake_supabase_client({})
    with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-key"}):
        with patch("services.odds.get_service_client", return_value=fake_client):
            with patch("services.odds.requests.get", side_effect=Exception("boom")):
                result = refresh_odds_lines("nfl")
    assert result is None
    assert fake_client._captured_upserts == []  # no partial cache write on failure


def test_refresh_odds_lines_no_api_key_returns_none_without_calling_api():
    with patch.dict(os.environ, {"THE_ODDS_API_KEY": ""}):
        with patch("services.odds.requests.get") as mock_get:
            result = refresh_odds_lines("nfl")
    assert result is None
    mock_get.assert_not_called()


def test_refresh_odds_lines_records_governor_on_error_response_and_returns_none():
    # The Odds API sends x-requests-remaining/-used on error responses too
    # (e.g. 401 OUT_OF_USAGE_CREDITS, 429) -- the governor must still be
    # recorded so can_refresh() becomes preventive instead of only updating
    # after a successful call.
    import requests as requests_mod

    fake_resp = MagicMock()
    fake_resp.headers = {"x-requests-remaining": "0", "x-requests-used": "500"}
    fake_resp.raise_for_status.side_effect = requests_mod.exceptions.HTTPError(
        "401 Client Error", response=fake_resp
    )
    fake_client = _fake_supabase_client({})

    with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-key"}):
        with patch("services.odds.get_service_client", return_value=fake_client):
            with patch("services.odds.requests.get", return_value=fake_resp) as mock_get:
                result = refresh_odds_lines("nfl")

    assert result is None
    mock_get.assert_called_once()
    upserted = fake_client._captured_upserts
    keys = {row["cache_key"] for row in upserted}
    assert keys == {"oddsapi:_meta"}  # no events row written, only the governor meta
    meta_row = next(r for r in upserted if r["cache_key"] == "oddsapi:_meta")
    assert meta_row["payload"]["remaining"] == 0
    assert meta_row["payload"]["used"] == 500


# ---------------------------------------------------------------------------
# credit governor -- can_refresh / credits_remaining
# ---------------------------------------------------------------------------

def test_can_refresh_false_when_remaining_below_floor():
    fake_client = _fake_supabase_client({"oddsapi:_meta": {"remaining": 40, "used": 460}})
    with patch("services.odds.get_service_client", return_value=fake_client):
        assert credits_remaining() == 40
        assert can_refresh(floor=60) is False


def test_can_refresh_true_when_remaining_above_floor():
    fake_client = _fake_supabase_client({"oddsapi:_meta": {"remaining": 200, "used": 300}})
    with patch("services.odds.get_service_client", return_value=fake_client):
        assert can_refresh(floor=60) is True


def test_can_refresh_true_when_unknown():
    fake_client = _fake_supabase_client({})  # cold cache, no meta row
    with patch("services.odds.get_service_client", return_value=fake_client):
        assert credits_remaining() is None
        assert can_refresh(floor=60) is True


# ---------------------------------------------------------------------------
# refresh_odds_props -- the only function allowed to call the per-event
# props endpoint. Per-event, not per-league, so the governor is re-checked
# before EVERY call (not just once up front).
# ---------------------------------------------------------------------------

def test_refresh_odds_props_calls_once_per_event_and_caches_each():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"id": "evt1", "bookmakers": []}
    fake_resp.raise_for_status.return_value = None
    fake_resp.headers = {"x-requests-remaining": "400", "x-requests-used": "100"}
    fake_client = _fake_supabase_client({})

    with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-key"}):
        with patch("services.odds.get_service_client", return_value=fake_client):
            with patch("services.odds.can_refresh", return_value=True):
                with patch("services.odds.requests.get", return_value=fake_resp) as mock_get:
                    refresh_odds_props("nfl", ["evt1", "evt2"])

    assert mock_get.call_count == 2
    urls = [c.args[0] if c.args else c.kwargs.get("url") for c in mock_get.call_args_list]
    assert any("events/evt1/odds" in u for u in urls)
    assert any("events/evt2/odds" in u for u in urls)
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["markets"] == "player_anytime_td"

    upserted = fake_client._captured_upserts
    keys = {row["cache_key"] for row in upserted}
    assert "oddsapi_props:evt1" in keys
    assert "oddsapi_props:evt2" in keys


def test_refresh_odds_props_stops_when_governor_floor_hit_mid_run():
    # can_refresh flips False after the first (real) check via a stateful
    # side_effect -- proves the loop checks the governor before EVERY call,
    # not just once up front, and stops rather than burning the rest of the
    # event list.
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"id": "evt1", "bookmakers": []}
    fake_resp.raise_for_status.return_value = None
    fake_resp.headers = {"x-requests-remaining": "61", "x-requests-used": "439"}
    fake_client = _fake_supabase_client({})

    call_count = {"n": 0}

    def _can_refresh_side_effect(floor=60):
        call_count["n"] += 1
        return call_count["n"] == 1  # True on the first check only

    with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-key"}):
        with patch("services.odds.get_service_client", return_value=fake_client):
            with patch("services.odds.can_refresh", side_effect=_can_refresh_side_effect):
                with patch("services.odds.requests.get", return_value=fake_resp) as mock_get:
                    refresh_odds_props("nfl", ["evt1", "evt2", "evt3"])

    mock_get.assert_called_once()  # only the first event was fetched
    upserted = fake_client._captured_upserts
    keys = {row["cache_key"] for row in upserted}
    assert keys == {"oddsapi_props:evt1", "oddsapi:_meta"}


def test_refresh_odds_props_never_raises_on_request_failure_and_continues():
    fake_client = _fake_supabase_client({})
    with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test-key"}):
        with patch("services.odds.get_service_client", return_value=fake_client):
            with patch("services.odds.can_refresh", return_value=True):
                with patch("services.odds.requests.get", side_effect=Exception("boom")) as mock_get:
                    refresh_odds_props("nfl", ["evt1", "evt2"])  # must not raise

    assert mock_get.call_count == 2  # one event failing doesn't stop the next
    assert fake_client._captured_upserts == []  # no partial cache write on failure


def test_refresh_odds_props_no_api_key_returns_without_calling_api():
    with patch.dict(os.environ, {"THE_ODDS_API_KEY": ""}):
        with patch("services.odds.requests.get") as mock_get:
            refresh_odds_props("nfl", ["evt1"])
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# featured_prop -- cache-only read of the anytime-TD market
# ---------------------------------------------------------------------------

def _props_payload():
    return {
        "id": "evt1",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_anytime_td",
                        "outcomes": [
                            {"name": "Travis Kelce", "price": 145},
                            {"name": "Isiah Pacheco", "price": -110},  # shortest price
                            {"name": "Justin Watson", "price": 260},
                        ],
                    },
                ],
            },
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [
                    {
                        "key": "player_anytime_td",
                        "outcomes": [
                            {"name": "Isiah Pacheco", "price": -105},  # still shortest overall
                        ],
                    },
                ],
            },
        ],
    }


def test_featured_prop_picks_shortest_priced_outcome():
    fake_client = _fake_supabase_client({"oddsapi_props:evt1": _props_payload()})
    with patch("services.odds.get_service_client", return_value=fake_client):
        result = featured_prop("evt1")

    assert result == {"label": "Anytime TD", "player": "Isiah Pacheco", "price": -110}


def test_featured_prop_returns_none_when_cache_cold():
    fake_client = _fake_supabase_client({})
    with patch("services.odds.get_service_client", return_value=fake_client):
        assert featured_prop("evt1") is None


def test_featured_prop_returns_none_when_no_anytime_td_market():
    payload = {
        "id": "evt1",
        "bookmakers": [
            {"key": "draftkings", "title": "DraftKings", "markets": [
                {"key": "h2h", "outcomes": [{"name": "Kansas City Chiefs", "price": -150}]},
            ]},
        ],
    }
    fake_client = _fake_supabase_client({"oddsapi_props:evt1": payload})
    with patch("services.odds.get_service_client", return_value=fake_client):
        assert featured_prop("evt1") is None


def test_featured_prop_returns_none_for_falsy_event_id_without_touching_supabase():
    with patch("services.odds.get_service_client") as mock_client:
        assert featured_prop(None) is None
        assert featured_prop("") is None
    mock_client.assert_not_called()
