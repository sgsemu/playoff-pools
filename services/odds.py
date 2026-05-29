"""The Odds API client, in-memory caching, and best-line computation.

We cache the response per sport_key for 6 hours since lines don't move fast
enough at this resolution for a friends pool. With ~4 sport keys polled at
worst once every 6 hours, monthly usage stays well inside the 500/month
free tier.

Public API:
    fetch_odds(league)         -> list[event_dict]   (raw Odds API events)
    best_by_outcome(event)     -> {outcome_name: {price, book_key, book_name}}
    enrich_calendar_with_best_odds(calendar)         -> mutates in place
    get_event_for_game(game)   -> matching Odds API event or None
"""
import os
import time
import requests

from services.bookmakers import bookmaker_keys_param


_BASE = "https://api.the-odds-api.com/v4/sports"
_TTL_SECONDS = 6 * 3600
_CACHE = {}  # {sport_key: (timestamp, data)}


# Our competition.league -> The Odds API sport_key.
_SPORT_KEY = {
    "nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "world_cup": "soccer_fifa_world_cup",
}


# ESPN sometimes uses slightly different team names than The Odds API. Map
# ESPN display name (lowercased) -> Odds API team name (also lowercased).
# Anything not in here is matched verbatim.
_NAME_ALIASES = {
    "türkiye": "turkey",
    "côte d'ivoire": "ivory coast",
    "czechia": "czech republic",
    "bosnia-herzegovina": "bosnia & herzegovina",
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


def fetch_odds(league):
    """Return cached raw events from The Odds API for the given league. Returns
    [] when no API key is set, the league has no sport_key mapping, or the
    request fails (with no warm cache to fall back on)."""
    key = sport_key(league)
    if not key:
        return []
    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        return []
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL_SECONDS:
        return cached[1]
    try:
        resp = requests.get(
            f"{_BASE}/{key}/odds",
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
                "bookmakers": bookmaker_keys_param(),
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return cached[1] if cached else []
    _CACHE[key] = (now, data)
    return data


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
