import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from services.sync import sync_competition_results


# ---------------------------------------------------------------------------
# Fake Supabase client double for game_results -- mirrors the recording-double
# style in tests/test_survivor_data.py (FakeSb), but scoped to just the one
# table sync.py touches. Real upsert-on-conflict semantics (keyed on
# espn_game_id) so tests can prove no duplicate rows and in-place updates.
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, verb, payload=None, on_conflict=None):
        self.store = store
        self.verb = verb
        self.payload = payload
        self.on_conflict = on_conflict
        self.filters = []

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, vals):
        self.filters.append((col, list(vals)))
        return self

    def select(self, cols="*"):
        return self

    def execute(self):
        if self.verb == "select":
            rows = self.store.rows
            for col, val in self.filters:
                if isinstance(val, list):
                    rows = [r for r in rows if r.get(col) in val]
                else:
                    rows = [r for r in rows if r.get(col) == val]
            return _Result(rows)
        if self.verb == "upsert":
            row = dict(self.payload)
            key_cols = [c.strip() for c in (self.on_conflict or "").split(",") if c.strip()]
            existing = None
            if key_cols:
                for r in self.store.rows:
                    if all(r.get(c) == row.get(c) for c in key_cols):
                        existing = r
                        break
            if existing is not None:
                existing.update(row)
                row = existing
            else:
                row.setdefault("id", f"id-{len(self.store.rows) + 1}")
                self.store.rows.append(row)
            self.store.upserts.append(row)
            return _Result([row])
        raise AssertionError(f"unhandled verb {self.verb}")


class _GameResultsStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.upserts = []

    def select(self, cols="*"):
        return _Query(self, "select")

    def upsert(self, row, on_conflict=None):
        return _Query(self, "upsert", payload=row, on_conflict=on_conflict)


def _sb_with_game_results(rows=None):
    store = _GameResultsStore(rows)

    def table(name):
        assert name == "game_results", f"unexpected table {name!r}"
        return store

    sb = MagicMock()
    sb.table.side_effect = table
    return sb, store


_COMP = {"id": "c-wc", "league": "world_cup", "espn_sport": "soccer",
         "espn_slug": "fifa.world", "event_filter": {}}


@patch("services.sync.fetch_competition_results")
def test_sync_upserts_all_games_but_only_counts_newly_completed(mock_fetch):
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 1, "away_score": 0, "is_complete": True, "stage": "group", "is_draw": False},
        {"espn_game_id": "g2", "home_team_id": 1, "away_team_id": 2,
         "home_score": 0, "away_score": 0, "is_complete": False, "stage": "group", "is_draw": False},
    ]
    sb, store = _sb_with_game_results()
    n = sync_competition_results(sb, _COMP)

    assert n == 1                       # only the completed game is "newly complete"
    assert len(store.rows) == 2         # both schedule + result rows are ingested
    by_id = {r["espn_game_id"]: r for r in store.rows}
    completed = by_id["g1"]
    assert completed["competition_id"] == "c-wc"
    assert completed["stage"] == "group"
    assert completed["is_draw"] is False
    assert completed["league"] == "world_cup"   # legacy column still written
    assert completed["is_complete"] is True

    scheduled = by_id["g2"]
    assert scheduled["is_complete"] is False
    assert scheduled["home_score"] == 0
    assert scheduled["away_score"] == 0


@patch("services.sync.today_et")
@patch("services.sync.fetch_competition_results")
def test_sync_game_date_derived_from_kickoff_not_sync_day(mock_fetch, mock_today):
    # Regression: sync moved from insert-once to upsert-always, which was
    # clobbering game_date with today's date on every run (breaking
    # playoff_day_count / the WC matchday counter). game_date must track the
    # game's own kickoff date in ET, not the day sync happens to run.
    import datetime
    mock_today.return_value = datetime.date(2026, 8, 22)  # "today" != kickoff date
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 0, "away_score": 0, "is_complete": False, "stage": "group",
         "is_draw": False, "kickoff_at": "2026-01-11T23:00Z"},
    ]
    sb, store = _sb_with_game_results()
    sync_competition_results(sb, _COMP)

    row = store.rows[0]
    # 23:00 UTC on Jan 11 is still Jan 11 in ET -- proves ET conversion, not
    # a naive UTC date, and proves it's NOT today_et()'s mocked date.
    assert row["game_date"] == "2026-01-11"
    assert row["game_date"] != mock_today.return_value.isoformat()


@patch("services.sync.fetch_competition_results")
def test_sync_game_date_falls_back_to_today_when_kickoff_missing(mock_fetch):
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 0, "away_score": 0, "is_complete": False, "stage": "group",
         "is_draw": False, "kickoff_at": None},
    ]
    sb, store = _sb_with_game_results()
    sync_competition_results(sb, _COMP)

    from services.espn_api import today_et
    assert store.rows[0]["game_date"] == today_et().isoformat()


@patch("services.sync.fetch_competition_results")
def test_sync_schedule_only_returns_zero_newly_completed(mock_fetch):
    # Pure schedule ingestion (nothing complete yet) must not trigger
    # downstream recalc/resolution -- that's what the 0 return signals.
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 0, "away_score": 0, "is_complete": False, "stage": "group", "is_draw": False},
        {"espn_game_id": "g2", "home_team_id": 1, "away_team_id": 2,
         "home_score": 0, "away_score": 0, "is_complete": False, "stage": "group", "is_draw": False},
    ]
    sb, store = _sb_with_game_results()
    assert sync_competition_results(sb, _COMP) == 0
    assert len(store.rows) == 2
    assert all(r["is_complete"] is False for r in store.rows)


@patch("services.sync.fetch_competition_results")
def test_sync_upserts_existing_row_in_place_not_duplicated(mock_fetch):
    # g1 was already ingested as scheduled (0-0, incomplete). It's now final.
    sb, store = _sb_with_game_results(rows=[
        {"id": "row-1", "espn_game_id": "g1", "competition_id": "c-wc",
         "home_team_id": 203, "away_team_id": 467,
         "home_score": 0, "away_score": 0, "is_complete": False,
         "stage": "group", "is_draw": False},
    ])
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 2, "away_score": 1, "is_complete": True, "stage": "group", "is_draw": False},
    ]
    n = sync_competition_results(sb, _COMP)

    assert n == 1                    # false -> true transition counts once
    assert len(store.rows) == 1      # updated in place, not duplicated
    row = store.rows[0]
    assert row["id"] == "row-1"      # same row, upserted
    assert row["is_complete"] is True
    assert row["home_score"] == 2
    assert row["away_score"] == 1


@patch("services.sync.fetch_competition_results")
def test_sync_already_complete_game_not_recounted_on_resync(mock_fetch):
    sb, store = _sb_with_game_results(rows=[
        {"id": "row-1", "espn_game_id": "g1", "competition_id": "c-wc",
         "home_team_id": 203, "away_team_id": 467,
         "home_score": 2, "away_score": 1, "is_complete": True,
         "stage": "group", "is_draw": False},
    ])
    mock_fetch.return_value = [
        {"espn_game_id": "g1", "home_team_id": 203, "away_team_id": 467,
         "home_score": 2, "away_score": 1, "is_complete": True, "stage": "group", "is_draw": False},
    ]
    assert sync_competition_results(sb, _COMP) == 0
    assert len(store.rows) == 1


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

    # comp "c1" has no "league" key, so sport_key(None) is None and the odds
    # block finds nothing to refresh -- odds_refreshed stays 0. Same comp has
    # no "league" == "nfl" either, so the props block's nfl_comp_ids is
    # empty and props_refreshed stays 0 regardless of what day it is.
    assert resp.get_json() == {"synced": 2, "odds_refreshed": 0, "props_refreshed": 0}
    # Pending survivor pool: resolve_and_apply must run even though its
    # draft_status is still 'pending' (survivor pools have no draft phase).
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[0][1]["id"] == "surv-pending"
    # Complete draft pool: recalculate_standings runs as before.
    mock_recalc.assert_called_once_with("draft-complete")
    # Pending draft pool: neither path fires (no draft, no standings yet).


# ---------------------------------------------------------------------------
# Vercel cron entrypoint -- odds-refresh step (Stage 2a): daily, governor-gated.
# ---------------------------------------------------------------------------

_ACTIVE_COMP_NFL = [{"id": "c-nfl", "league": "nfl"}]


def test_sync_games_cron_refreshes_odds_when_governor_allows():
    from api.cron import sync_games as cron_mod

    with patch.object(cron_mod, "get_service_client", return_value=_PoolsClient([])), \
         patch.object(cron_mod, "competitions_for_active_pools", return_value=_ACTIVE_COMP_NFL), \
         patch.object(cron_mod, "sync_competition_results", return_value=0), \
         patch("services.odds.can_refresh", return_value=True), \
         patch("services.odds.refresh_odds_lines") as mock_refresh:
        client = cron_mod.app.test_client()
        resp = client.get("/api/cron/sync-games")

    mock_refresh.assert_called_once_with("nfl")
    # props_refreshed stays 0 here regardless of real-world weekday: the NFL
    # comp is active so _current_week_odds_event_ids would run, but
    # _PoolsClient only stubs the "pools" table -- the AssertionError it
    # raises on "game_results" is swallowed by the props block's broad
    # except, same as any other props failure.
    assert resp.get_json() == {"synced": 0, "odds_refreshed": 1, "props_refreshed": 0}


def test_sync_games_cron_skips_odds_refresh_when_governor_floor_hit():
    from api.cron import sync_games as cron_mod

    with patch.object(cron_mod, "get_service_client", return_value=_PoolsClient([])), \
         patch.object(cron_mod, "competitions_for_active_pools", return_value=_ACTIVE_COMP_NFL), \
         patch.object(cron_mod, "sync_competition_results", return_value=0), \
         patch("services.odds.can_refresh", return_value=False), \
         patch("services.odds.refresh_odds_lines") as mock_refresh:
        client = cron_mod.app.test_client()
        resp = client.get("/api/cron/sync-games")

    mock_refresh.assert_not_called()
    # can_refresh is patched to always return False, which also gates the
    # props block (same governor check) -- props_refreshed stays 0.
    assert resp.get_json() == {"synced": 0, "odds_refreshed": 0, "props_refreshed": 0}


# ---------------------------------------------------------------------------
# Vercel cron entrypoint -- props-refresh step (Thu-Sun, governor-gated).
# _current_week_odds_event_ids is patched directly in these tests (rather
# than exercised through a fake game_results/teams table) so the assertions
# stay focused on the weekday + governor gating this step adds; that
# resolver's own week-picking logic isn't covered here.
# ---------------------------------------------------------------------------

import datetime as _dt


def _et(y, m, d, hh=12):
    from zoneinfo import ZoneInfo
    return _dt.datetime(y, m, d, hh, 0, tzinfo=ZoneInfo("America/New_York"))


def test_sync_games_cron_refreshes_props_on_thursday_when_governor_allows():
    from api.cron import sync_games as cron_mod

    thursday = _et(2026, 8, 20)  # 2026-08-20 is a Thursday
    assert thursday.strftime("%a") == "Thu"

    with patch.object(cron_mod, "get_service_client", return_value=_PoolsClient([])), \
         patch.object(cron_mod, "competitions_for_active_pools", return_value=_ACTIVE_COMP_NFL), \
         patch.object(cron_mod, "sync_competition_results", return_value=0), \
         patch.object(cron_mod, "_now_et", return_value=thursday), \
         patch.object(cron_mod, "_current_week_odds_event_ids", return_value=["evt1", "evt2"]), \
         patch("services.odds.can_refresh", return_value=True), \
         patch("services.odds.refresh_odds_props") as mock_refresh_props, \
         patch("services.odds.refresh_odds_lines"):
        client = cron_mod.app.test_client()
        resp = client.get("/api/cron/sync-games")

    mock_refresh_props.assert_called_once_with("nfl", ["evt1", "evt2"])
    assert resp.get_json()["props_refreshed"] == 2


def test_sync_games_cron_skips_props_on_a_non_thu_sun_day():
    from api.cron import sync_games as cron_mod

    monday = _et(2026, 8, 17)  # 2026-08-17 is a Monday
    assert monday.strftime("%a") == "Mon"

    with patch.object(cron_mod, "get_service_client", return_value=_PoolsClient([])), \
         patch.object(cron_mod, "competitions_for_active_pools", return_value=_ACTIVE_COMP_NFL), \
         patch.object(cron_mod, "sync_competition_results", return_value=0), \
         patch.object(cron_mod, "_now_et", return_value=monday), \
         patch.object(cron_mod, "_current_week_odds_event_ids", return_value=["evt1"]), \
         patch("services.odds.can_refresh", return_value=True), \
         patch("services.odds.refresh_odds_props") as mock_refresh_props, \
         patch("services.odds.refresh_odds_lines"):
        client = cron_mod.app.test_client()
        resp = client.get("/api/cron/sync-games")

    mock_refresh_props.assert_not_called()
    assert resp.get_json()["props_refreshed"] == 0


def test_sync_games_cron_skips_props_on_thursday_when_governor_floor_hit():
    from api.cron import sync_games as cron_mod

    thursday = _et(2026, 8, 20)

    with patch.object(cron_mod, "get_service_client", return_value=_PoolsClient([])), \
         patch.object(cron_mod, "competitions_for_active_pools", return_value=_ACTIVE_COMP_NFL), \
         patch.object(cron_mod, "sync_competition_results", return_value=0), \
         patch.object(cron_mod, "_now_et", return_value=thursday), \
         patch.object(cron_mod, "_current_week_odds_event_ids", return_value=["evt1"]), \
         patch("services.odds.can_refresh", return_value=False), \
         patch("services.odds.refresh_odds_props") as mock_refresh_props, \
         patch("services.odds.refresh_odds_lines"):
        client = cron_mod.app.test_client()
        resp = client.get("/api/cron/sync-games")

    mock_refresh_props.assert_not_called()
    assert resp.get_json()["props_refreshed"] == 0
