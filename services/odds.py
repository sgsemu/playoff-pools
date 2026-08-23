"""The Odds API client, Supabase-backed caching, and best-line computation.

Reads are cache-ONLY: fetch_odds() (and the OddsPapi/Caesars read helpers)
NEVER call the upstream APIs -- they only read the `odds_cache` Supabase
table. That is deliberate: on Vercel serverless every cold render is a fresh
process, so an in-process cache protects nothing and each render could
otherwise re-hit The Odds API and drain the 500/month free tier. The ONLY
functions allowed to call the upstream APIs are refresh_odds_lines() and the
OddsPapi refresh_* counterparts, which are meant to be invoked by a
scheduled cron job -- never on a request path.

A secondary source, OddsPapi, supplies Caesars odds (which The Odds API
doesn't currently return for our sports). When OddsPapi has a Caesars line
that beats the best Odds API price, the chip falls to Caesars + the user's
Caesars referral link.

Public API:
    fetch_odds(league)         -> list[event_dict]   (raw Odds API events,
                                   cache-only -- see module docstring)
    refresh_odds_lines(league) -> live Odds API call + cache write + credit
                                   governor update; cron-only, never called
                                   on a request path
    credits_remaining()        -> last-known remaining Odds API credits, or
                                   None if unknown
    can_refresh(floor=60)      -> False only when known remaining < floor
    best_by_outcome(event)     -> {outcome_name: {price, book_key, book_name}}
    best_spread_by_team(event) -> {team_name: point}
    best_total(event)          -> {point, over: {price, book_key, book_name},
                                    under: {...}} or None
    game_odds(event)           -> {moneyline, spread, total} bundle
    enrich_calendar_with_best_odds(calendar)         -> mutates in place
    get_event_for_game(game)   -> matching Odds API event or None
"""
import os
from datetime import datetime, timezone

import requests

from services.bookmakers import bookmaker_keys_param
from services.supabase_client import get_service_client


_BASE = "https://api.the-odds-api.com/v4/sports"

# Per-process memo so one render doesn't re-query Supabase for the same
# cache_key twice -- NOT the source of truth. Supabase (odds_cache table) is.
_MEMO = {}


# Our competition.league -> The Odds API sport_key.
_SPORT_KEY = {
    "nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "world_cup": "soccer_fifa_world_cup",
    "nfl": "americanfootball_nfl",
}


# ESPN sometimes uses slightly different team names than The Odds API. Map
# ESPN display name (lowercased) -> Odds API team name (also lowercased).
# Anything not in here is matched verbatim.
_NAME_ALIASES = {
    "türkiye": "turkey",
    "côte d'ivoire": "ivory coast",
    "czechia": "czech republic",
    "bosnia-herzegovina": "bosnia & herzegovina",
    "curaçao": "curacao",          # ESPN: no cedilla; OddsAPI: with cedilla
    "congo dr": "dr congo",        # ESPN: word order swapped from OddsAPI
    # Defensive aliases — both sides apply, so they remain harmless if naming
    # already agrees (current state for these four):
    "usa": "united states",
    "south korea": "korea republic",
}


def sport_key(league):
    return _SPORT_KEY.get(league)


def _norm(name):
    if not name:
        return ""
    n = name.strip().lower()
    return _NAME_ALIASES.get(n, n)


def _cache_get(cache_key):
    """Read `cache_key`'s payload from the odds_cache Supabase table. Returns
    None on a cold/missing row OR any error (network, missing table, bad
    creds) so callers can treat a broken cache the same as an empty one."""
    if cache_key in _MEMO:
        return _MEMO[cache_key]
    try:
        client = get_service_client()
        resp = (
            client.table("odds_cache")
            .select("payload")
            .eq("cache_key", cache_key)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        payload = rows[0]["payload"] if rows else None
    except Exception:
        return None
    _MEMO[cache_key] = payload
    return payload


def _cache_put(cache_key, payload):
    """Upsert `cache_key`'s payload + fetched_at=now into odds_cache.
    Best-effort: swallows errors so a Supabase hiccup can't crash a refresh."""
    try:
        client = get_service_client()
        client.table("odds_cache").upsert(
            {
                "cache_key": cache_key,
                "payload": payload,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="cache_key",
        ).execute()
    except Exception:
        return
    _MEMO[cache_key] = payload


def fetch_odds(league):
    """Return cached raw events from The Odds API for the given league. Pure
    cache read -- NEVER calls The Odds API (see module docstring). Returns []
    when the league has no sport_key mapping or the cache is cold/empty."""
    key = sport_key(league)
    if not key:
        return []
    payload = _cache_get(f"oddsapi:{key}")
    return payload if payload else []


def refresh_odds_lines(league):
    """Call The Odds API live for `league` and refresh the cache. The ONLY
    function that hits The Odds API's /odds endpoint -- meant to be invoked
    by the scheduled cron, never on a request path. On success, writes
    `oddsapi:<sport_key>` and updates the `oddsapi:_meta` credit governor
    from the response headers. Returns the fetched data, or None on failure
    (no API key, no sport_key mapping, or the request itself failing).
    Never raises."""
    key = sport_key(league)
    if not key:
        return None
    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        resp = requests.get(
            f"{_BASE}/{key}/odds",
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "bookmakers": bookmaker_keys_param(),
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    _cache_put(f"oddsapi:{key}", data)
    _record_governor(resp)
    return data


def _record_governor(resp):
    """Store The Odds API's credit-remaining/used response headers under the
    `oddsapi:_meta` cache key. Best-effort -- unparseable/missing headers
    become None rather than raising."""
    def _to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    _cache_put(
        "oddsapi:_meta",
        {
            "remaining": _to_int(resp.headers.get("x-requests-remaining")),
            "used": _to_int(resp.headers.get("x-requests-used")),
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )


def credits_remaining():
    """Last-known remaining Odds API credits (from the most recent
    refresh_odds_lines() response headers), or None if unknown (cache cold,
    or the header was missing/unparseable on the last refresh)."""
    meta = _cache_get("oddsapi:_meta")
    if not meta:
        return None
    return meta.get("remaining")


def can_refresh(floor=60):
    """False only when remaining credits are KNOWN to be below `floor`. True
    when remaining is unknown (fail open -- the live API call is the
    authoritative check; this is just a cheap pre-flight guard for the
    cron)."""
    remaining = credits_remaining()
    if remaining is None:
        return True
    return remaining >= floor


def _decimal(american):
    """American odds -> decimal payout multiplier. Higher = better for bettor."""
    if american is None:
        return None
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / abs(american)


def best_by_outcome(event):
    """For one Odds API event, return {outcome_name: {price, book_key, book_name}}
    picking the bookmaker with the highest decimal price for each outcome.
    Outcome names match the Odds API: the home_team name, the away_team name,
    or 'Draw' for soccer 3-way."""
    best = {}  # name -> (decimal, american_price, book_key, book_name)
    for book in event.get("bookmakers", []) or []:
        b_key = book.get("key")
        b_name = book.get("title") or b_key
        for market in book.get("markets", []) or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []) or []:
                name = outcome.get("name")
                price = outcome.get("price")
                d = _decimal(price)
                if name is None or d is None:
                    continue
                cur = best.get(name)
                if cur is None or d > cur[0]:
                    best[name] = (d, price, b_key, b_name)
    return {
        n: {"price": v[1], "book_key": v[2], "book_name": v[3]}
        for n, v in best.items()
    }


def best_spread_by_team(event):
    """For one Odds API event, return {team_name: point} picking the bookmaker
    with the highest decimal price for each team's spread outcome (mirrors
    best_by_outcome, but reads the 'spreads' market and returns the point
    instead of the price). The favorite's point is negative, the underdog's
    positive. Teams with no spreads market on any bookmaker are omitted."""
    best = {}  # name -> (decimal, point)
    for book in event.get("bookmakers", []) or []:
        for market in book.get("markets", []) or []:
            if market.get("key") != "spreads":
                continue
            for outcome in market.get("outcomes", []) or []:
                name = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")
                d = _decimal(price)
                if name is None or d is None or point is None:
                    continue
                cur = best.get(name)
                if cur is None or d > cur[0]:
                    best[name] = (d, point)
    return {n: v[1] for n, v in best.items()}


def best_total(event):
    """For one Odds API event, return {"point": <number>, "over": {price,
    book_key, book_name}, "under": {...}} picking the bookmaker with the
    highest decimal price for each side of the 'totals' market (mirrors
    best_by_outcome/best_spread_by_team). Returns None if no bookmaker has a
    totals market for this event. Either "over" or "under" may be absent if
    only one side has a price anywhere."""
    best = {}  # "Over"/"Under" -> (decimal, price, book_key, book_name, point)
    for book in event.get("bookmakers", []) or []:
        b_key = book.get("key")
        b_name = book.get("title") or b_key
        for market in book.get("markets", []) or []:
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []) or []:
                name = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")
                d = _decimal(price)
                if name is None or d is None or point is None:
                    continue
                cur = best.get(name)
                if cur is None or d > cur[0]:
                    best[name] = (d, price, b_key, b_name, point)
    if not best:
        return None
    point = next(iter(best.values()))[4]
    result = {"point": point}
    if "Over" in best:
        v = best["Over"]
        result["over"] = {"price": v[1], "book_key": v[2], "book_name": v[3]}
    if "Under" in best:
        v = best["Under"]
        result["under"] = {"price": v[1], "book_key": v[2], "book_name": v[3]}
    return result


def game_odds(event):
    """Bundle all three markets for one Odds API event. Returns
    {"moneyline": {...}, "spread": {...}, "total": {...} | None} -- an empty
    bundle (moneyline={}, spread={}, total=None) if event is falsy."""
    if not event:
        return {"moneyline": {}, "spread": {}, "total": None}
    return {
        "moneyline": best_by_outcome(event),
        "spread": best_spread_by_team(event),
        "total": best_total(event),
    }


def _event_pairs_index(events):
    """Index Odds API events by (home_lower, away_lower) AND (away_lower, home_lower)
    so we match either home/away assignment from ESPN."""
    idx = {}
    for ev in events:
        h = _norm(ev.get("home_team"))
        a = _norm(ev.get("away_team"))
        if h and a:
            idx[(h, a)] = ev
            idx[(a, h)] = ev
    return idx


def get_event_for_game(game):
    """Look up the Odds API event matching a calendar game by team names.
    Returns None if no API key, no league, or no match."""
    league = game.get("league")
    if not league:
        return None
    events = fetch_odds(league)
    if not events:
        return None
    idx = _event_pairs_index(events)
    h = _norm((game.get("home") or {}).get("name"))
    a = _norm((game.get("away") or {}).get("name"))
    return idx.get((h, a))


# ---------------------------------------------------------------------------
# OddsPapi (secondary source) — currently used to fill in Caesars odds since
# The Odds API doesn't return Caesars for our sports right now. Add new
# leagues here as we find their OddsPapi sportId + tournamentId.

_ODDSPAPI_BASE = "https://api.oddspapi.io/v4"
_ODDSPAPI_BOOK = "caesars"      # which OddsPapi bookmaker we're after
_ODDSPAPI_MARKET = 101          # 1X2 moneyline market id
_ODDSPAPI_OUTCOMES = {101: "home", 102: "draw", 103: "away"}

_ODDSPAPI = {
    "world_cup": {"sport_id": 10, "tournament_ids": [16]},
    # NBA / NHL tournament ids TBD — once added, Caesars supplement kicks in
    # automatically for those competitions too.
}

def _oddspapi_get(path, params):
    api_key = os.environ.get("ODDSPAPI_API_KEY", "").strip()
    if not api_key:
        return None, "no-key"
    params = dict(params or {})
    params["apiKey"] = api_key
    try:
        resp = requests.get(f"{_ODDSPAPI_BASE}{path}", params=params, timeout=10)
        if resp.status_code != 200:
            return None, f"http-{resp.status_code}"
        return resp.json(), None
    except Exception as exc:
        return None, f"err-{exc}"


def _oddspapi_participants(sport_id):
    """Cache-only `{id_str: name}` map for an OddsPapi sport. Pure cache
    read -- NEVER calls OddsPapi (see refresh_oddspapi_participants). Returns
    {} when the cache is cold/empty."""
    payload = _cache_get(f"oddspapi_participants:{sport_id}")
    return payload if payload else {}


def refresh_oddspapi_participants(sport_id):
    """Call OddsPapi live for `sport_id`'s participants map and refresh the
    cache. The ONLY function that hits OddsPapi's /participants endpoint --
    cron-only, never a request path. Returns the fetched map, or None on
    failure. Never raises."""
    data, _err = _oddspapi_get("/participants", {"sportId": sport_id})
    if not isinstance(data, dict):
        return None
    _cache_put(f"oddspapi_participants:{sport_id}", data)
    return data


def _fetch_oddspapi_caesars(league):
    """Cache-only list of Caesars odds fixtures for a league. Pure cache
    read -- NEVER calls OddsPapi (see refresh_oddspapi_caesars). Returns []
    when unconfigured or the cache is cold/empty."""
    cfg = _ODDSPAPI.get(league)
    if not cfg or not cfg.get("tournament_ids"):
        return []
    payload = _cache_get(f"oddspapi:{league}")
    return payload if payload else []


def refresh_oddspapi_caesars(league):
    """Call OddsPapi live for `league`'s Caesars fixtures and refresh the
    cache. The ONLY function that hits OddsPapi's /odds-by-tournaments
    endpoint -- cron-only, never a request path. Returns the fetched
    fixtures list, or None when unconfigured/on total failure. Never raises."""
    cfg = _ODDSPAPI.get(league)
    if not cfg or not cfg.get("tournament_ids"):
        return None
    fixtures = []
    for tid in cfg["tournament_ids"]:
        data, _err = _oddspapi_get("/odds-by-tournaments", {
            "tournamentIds": tid,
            "bookmaker": _ODDSPAPI_BOOK,
            "marketIds": _ODDSPAPI_MARKET,
            "oddsFormat": "american",
        })
        if isinstance(data, list):
            fixtures.extend(data)
    _cache_put(f"oddspapi:{league}", fixtures)
    return fixtures


def _caesars_price_int(fixture, outcome_id):
    """Pull the American moneyline price (int) for an OddsPapi outcome, or None."""
    try:
        outcome = (fixture.get("bookmakerOdds", {})
                          .get(_ODDSPAPI_BOOK, {})
                          .get("markets", {})
                          .get(str(_ODDSPAPI_MARKET), {})
                          .get("outcomes", {})
                          .get(str(outcome_id), {})
                          .get("players", {})
                          .get("0", {}))
        if not outcome.get("active"):
            return None
        return int(outcome["priceAmerican"])
    except (KeyError, TypeError, ValueError):
        return None


def _caesars_index_for_league(league):
    """Return {(home_norm, away_norm): {home_name, away_name, prices}} for the
    Caesars fixtures we have. Both orientations indexed."""
    fixtures = _fetch_oddspapi_caesars(league)
    if not fixtures:
        return {}
    cfg = _ODDSPAPI[league]
    names = _oddspapi_participants(cfg["sport_id"])
    idx = {}
    for fx in fixtures:
        p1 = str(fx.get("participant1Id") or "")
        p2 = str(fx.get("participant2Id") or "")
        home_name = names.get(p1)
        away_name = names.get(p2)
        if not home_name or not away_name:
            continue
        entry = {
            "home_name": home_name,
            "away_name": away_name,
            "home_price": _caesars_price_int(fx, 101),
            "draw_price": _caesars_price_int(fx, 102),
            "away_price": _caesars_price_int(fx, 103),
        }
        h, a = _norm(home_name), _norm(away_name)
        idx[(h, a)] = entry
        idx[(a, h)] = entry
    return idx


def caesars_bookmaker_for_event(odds_api_event, league):
    """Return a Caesars `bookmaker` dict matching The Odds API's shape so the
    detail-page template can render it alongside the other books. Outcomes are
    labelled with the Odds API team names so price comparison (best_by_outcome)
    treats them as the same outcomes. Returns None when no Caesars line is
    available for this game."""
    idx = _caesars_index_for_league(league)
    if not idx:
        return None
    home_team = odds_api_event.get("home_team") or ""
    away_team = odds_api_event.get("away_team") or ""
    entry = idx.get((_norm(home_team), _norm(away_team)))
    if not entry:
        return None
    swap = _norm(entry["home_name"]) == _norm(away_team)
    home_price = entry["away_price"] if swap else entry["home_price"]
    away_price = entry["home_price"] if swap else entry["away_price"]
    outcomes = []
    if home_price is not None:
        outcomes.append({"name": home_team, "price": home_price})
    if away_price is not None:
        outcomes.append({"name": away_team, "price": away_price})
    if entry["draw_price"] is not None:
        outcomes.append({"name": "Draw", "price": entry["draw_price"]})
    if not outcomes:
        return None
    return {
        "key": "caesars",
        "title": "Caesars Sportsbook",
        "markets": [{"key": "h2h", "outcomes": outcomes}],
    }


def _maybe_promote_caesars(best, league, odds_api_event):
    """If OddsPapi has a Caesars line for this game that beats the current best
    for any outcome, overwrite that outcome in `best`. Mutates `best`."""
    idx = _caesars_index_for_league(league)
    if not idx:
        return
    home_name = odds_api_event.get("home_team") or ""
    away_name = odds_api_event.get("away_team") or ""
    entry = idx.get((_norm(home_name), _norm(away_name)))
    if not entry:
        return
    # Reconcile orientation: OddsPapi's "home" may be Odds API's "away".
    swap = _norm(entry["home_name"]) == _norm(away_name)
    proposals = {
        away_name if swap else home_name: entry["home_price"],
        home_name if swap else away_name: entry["away_price"],
        "Draw": entry["draw_price"],
    }
    caesars_payload = {"book_key": "caesars", "book_name": "Caesars Sportsbook"}
    for outcome_name, american_price in proposals.items():
        if american_price is None:
            continue
        new_dec = _decimal(american_price)
        existing = best.get(outcome_name)
        if existing is None or new_dec > _decimal(existing["price"]):
            best[outcome_name] = {"price": american_price, **caesars_payload}


def enrich_calendar_with_best_odds(calendar):
    """Attach `best_odds = {home, away, draw}` to each game in `calendar` (in
    place). Mutates the input. Games without a matching Odds API event get
    no key set, so templates can guard with `{% if g.best_odds %}`."""
    # Group games by league so we fetch each sport_key at most once per call.
    by_league = {}
    for date_data in calendar.values():
        for g in date_data.get("games", []) or []:
            by_league.setdefault(g.get("league"), []).append(g)
    for league, games in by_league.items():
        if not league:
            continue
        events = fetch_odds(league)
        if not events:
            continue
        idx = _event_pairs_index(events)
        for g in games:
            h = _norm((g.get("home") or {}).get("name"))
            a = _norm((g.get("away") or {}).get("name"))
            ev = idx.get((h, a))
            if not ev:
                continue
            best = best_by_outcome(ev)
            _maybe_promote_caesars(best, league, ev)
            home_team = ev.get("home_team")
            away_team = ev.get("away_team")
            # The Odds API home_team / away_team may be assigned the opposite
            # way around from ESPN — best is keyed by the Odds API name, so we
            # need to look up by Odds API name and then re-tag to ESPN home/away.
            home_best = best.get(home_team) if home_team else None
            away_best = best.get(away_team) if away_team else None
            # If ESPN's "home" name matches Odds API's away_team (swapped sides),
            # remap so home_best is the bettor's home pick.
            if _norm(home_team) == _norm((g.get("away") or {}).get("name")):
                home_best, away_best = away_best, home_best
            g["best_odds"] = {
                "home": home_best,
                "away": away_best,
                "draw": best.get("Draw"),
            }
