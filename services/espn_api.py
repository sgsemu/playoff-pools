import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ESPN_NBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_NHL_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
ESPN_BASE = ESPN_NBA_BASE  # backward compat

# Anchor "today" to Eastern Time so Vercel (UTC) doesn't flip the date
# after 8 PM ET during DST.
_ET = ZoneInfo("America/New_York")


def today_et():
    return datetime.now(_ET).date()


def fetch_upcoming_games(days=7):
    """Fetch upcoming playoff games grouped by date, split by league."""
    from collections import OrderedDict

    by_date = OrderedDict()

    for d in range(days):
        game_date = today_et() + timedelta(days=d)
        dt = game_date.strftime("%Y%m%d")
        date_label = game_date.strftime("%a, %b %-d")  # e.g. "Fri, Apr 18"
        date_key = game_date.isoformat()

        for base, league in [(ESPN_NBA_BASE, "nba"), (ESPN_NHL_BASE, "nhl")]:
            try:
                resp = requests.get(f"{base}/scoreboard", params={"dates": dt}, timeout=10)
                resp.raise_for_status()
                for event in resp.json().get("events", []):
                    if event.get("season", {}).get("type", 0) != 3:
                        continue
                    comp = event["competitions"][0]
                    # Only show games that haven't started. Completed ones go
                    # to Recent Games, in-progress ones to the Live section.
                    if comp["status"]["type"].get("state") != "pre":
                        continue
                    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
                    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")

                    if date_key not in by_date:
                        by_date[date_key] = {"label": date_label, "nba": [], "nhl": []}

                    by_date[date_key][league].append({
                        "home_name": home["team"].get("displayName", "?"),
                        "home_abbr": home["team"].get("abbreviation", "?"),
                        "away_name": away["team"].get("displayName", "?"),
                        "away_abbr": away["team"].get("abbreviation", "?"),
                        "status": comp["status"]["type"]["shortDetail"],
                    })
            except Exception:
                continue

    return by_date


def fetch_calendar_games(competitions, days_back=7, days_forward=7):
    """Return every relevant game in a window around today, grouped by date,
    across the given competitions. Each game carries league/stage/state/scores
    and primary team colors so the calendar can render in one pass. Days with
    no games are omitted."""
    from collections import OrderedDict
    from services.team_colors import team_color

    by_date = OrderedDict()
    for d in range(-days_back, days_forward + 1):
        game_date = today_et() + timedelta(days=d)
        dt = game_date.strftime("%Y%m%d")
        date_label = game_date.strftime("%a, %b %-d")
        date_key = game_date.isoformat()

        for comp in competitions or []:
            try:
                games = fetch_competition_results(comp, dates=dt)
            except Exception:
                continue
            for game in games:
                home_color = team_color(comp.get("league", ""), game["home_team_id"])
                away_color = team_color(comp.get("league", ""), game["away_team_id"])
                by_date.setdefault(date_key, {"label": date_label, "games": []})
                by_date[date_key]["games"].append({
                    "espn_game_id": game["espn_game_id"],
                    "league": comp.get("league", ""),
                    "stage": game.get("stage"),
                    "state": game.get("state", "pre"),
                    "is_draw": game.get("is_draw", False),
                    "status_detail": game.get("status_detail", ""),
                    "home": {
                        "id": game["home_team_id"],
                        "abbr": game.get("home_team_abbr", "?"),
                        "name": game.get("home_team_name", "?"),
                        "score": game["home_score"],
                        "color": home_color,
                    },
                    "away": {
                        "id": game["away_team_id"],
                        "abbr": game.get("away_team_abbr", "?"),
                        "name": game.get("away_team_name", "?"),
                        "score": game["away_score"],
                        "color": away_color,
                    },
                })
    return by_date


def fetch_live_games(competitions):
    """Return in-progress games across the given competitions with current
    scores and status. Each row is tagged with its competition's league."""
    out = []
    for comp in competitions or []:
        base = (f"https://site.api.espn.com/apis/site/v2/sports/"
                f"{comp['espn_sport']}/{comp['espn_slug']}")
        try:
            data = requests.get(f"{base}/scoreboard", headers={"User-Agent": "Mozilla/5.0"},
                                timeout=8).json()
        except Exception:
            continue
        for ev in data.get("events", []):
            comp_node = ev["competitions"][0]
            s = comp_node["status"]["type"]
            if s.get("state") != "in":
                continue
            home = next(c for c in comp_node["competitors"] if c["homeAway"] == "home")
            away = next(c for c in comp_node["competitors"] if c["homeAway"] == "away")
            out.append({
                "league": comp.get("league", ""),
                "status": s.get("shortDetail", ""),
                "home_abbr": home["team"].get("abbreviation", "?"),
                "home_name": home["team"].get("displayName", "?"),
                "home_score": int(home["score"]) if str(home.get("score", "")).isdigit() else 0,
                "away_abbr": away["team"].get("abbreviation", "?"),
                "away_name": away["team"].get("displayName", "?"),
                "away_score": int(away["score"]) if str(away.get("score", "")).isdigit() else 0,
            })
    return out


def fetch_scoreboard(date=None):
    url = f"{ESPN_BASE}/scoreboard"
    params = {}
    if date:
        params["dates"] = date  # format: YYYYMMDD

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for event in data.get("events", []):
        # Only sync playoff games (season type 3)
        if event.get("season", {}).get("type", 0) != 3:
            continue

        comp = event["competitions"][0]
        competitors = comp["competitors"]
        home = next(c for c in competitors if c["homeAway"] == "home")
        away = next(c for c in competitors if c["homeAway"] == "away")
        is_complete = comp["status"]["type"]["completed"]

        games.append({
            "espn_game_id": event["id"],
            "home_team_id": int(home["team"]["id"]),
            "away_team_id": int(away["team"]["id"]),
            "home_score": int(home["score"]) if home.get("score") else 0,
            "away_score": int(away["score"]) if away.get("score") else 0,
            "is_complete": is_complete,
        })

    return games


def fetch_game_boxscore(espn_game_id):
    url = f"{ESPN_BASE}/summary"
    resp = requests.get(url, params={"event": espn_game_id}, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    players = []
    for team_data in data.get("boxscore", {}).get("players", []):
        team_id = int(team_data["team"]["id"])
        for stat_group in team_data.get("statistics", []):
            for athlete in stat_group.get("athletes", []):
                stats = athlete.get("stats", [])
                # ESPN box score stat order: PTS, REB, AST, STL, BLK, TO, MIN (varies)
                # We'll parse by checking the labels
                players.append({
                    "espn_player_id": int(athlete["athlete"]["id"]),
                    "name": athlete["athlete"]["displayName"],
                    "team_id": team_id,
                    "points": int(stats[0]) if len(stats) > 0 and stats[0].isdigit() else 0,
                    "rebounds": int(stats[1]) if len(stats) > 1 and stats[1].isdigit() else 0,
                    "assists": int(stats[2]) if len(stats) > 2 and stats[2].isdigit() else 0,
                })

    return players


def fetch_playoff_teams():
    url = f"{ESPN_BASE}/teams"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    teams = []
    for t in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        team = t["team"]
        # Group ID 4 = Eastern, 5 = Western (approximate -- may need verification)
        conference = "East" if team.get("groups", {}).get("id") == "4" else "West"
        teams.append({
            "id": int(team["id"]),
            "name": team["displayName"],
            "abbreviation": team["abbreviation"],
            "conference": conference,
        })

    return teams


def fetch_team_roster(team_id):
    url = f"{ESPN_BASE}/teams/{team_id}/roster"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    players = []
    for athlete in data.get("athletes", []):
        players.append({
            "id": int(athlete["id"]),
            "name": athlete["displayName"],
            "team_id": team_id,
            "position": athlete.get("position", {}).get("abbreviation", ""),
        })

    return players


def fetch_player_stats(player_id):
    """Fetch a player's season averages from ESPN."""
    url = f"{ESPN_BASE}/players/{player_id}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for cat in data.get("statistics", []):
            splits = cat.get("splits", [])
            if splits:
                stats = splits[0].get("stats", [])
                return {
                    "ppg": float(stats[0]) if stats else 0.0,
                }
    except Exception:
        pass
    return {"ppg": 0.0}


# ── NHL Functions ──────────────────────────────────────────────────────

def fetch_nhl_scoreboard(date=None):
    url = f"{ESPN_NHL_BASE}/scoreboard"
    params = {}
    if date:
        params["dates"] = date

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for event in data.get("events", []):
        # Only sync playoff games (season type 3)
        if event.get("season", {}).get("type", 0) != 3:
            continue

        comp = event["competitions"][0]
        competitors = comp["competitors"]
        home = next(c for c in competitors if c["homeAway"] == "home")
        away = next(c for c in competitors if c["homeAway"] == "away")
        is_complete = comp["status"]["type"]["completed"]

        games.append({
            "espn_game_id": event["id"],
            "home_team_id": int(home["team"]["id"]),
            "away_team_id": int(away["team"]["id"]),
            "home_score": int(home["score"]) if home.get("score") else 0,
            "away_score": int(away["score"]) if away.get("score") else 0,
            "is_complete": is_complete,
            "league": "nhl",
        })

    return games


# ESPN season.slug -> our competition stage key. Group-stage confirmed live;
# knockout slugs are FIFA's standard bracket names — verify once the bracket
# is set (Task 1 probe only sees group-stage pre-tournament).
STAGE_SLUGS = {
    "world_cup": {
        "group-stage": "group",
        "round-of-32": "r32",
        "round-of-16": "r16",
        "quarterfinals": "qf",
        "semifinals": "sf",
        "third-place": "third_place",
        "final": "final",
    },
}


def resolve_stage(league, season_slug):
    """Map an ESPN season.slug to our stage key, or None if unrecognized."""
    return STAGE_SLUGS.get(league, {}).get(season_slug)


def fetch_competition_results(competition, dates=None):
    """Fetch results for one competition. Returns a list of game dicts with
    competition-agnostic fields the sync layer writes. `dates` is an optional
    YYYYMMDD string; without it, ESPN returns the current scoreboard."""
    base = (f"https://site.api.espn.com/apis/site/v2/sports/"
            f"{competition['espn_sport']}/{competition['espn_slug']}")
    params = {}
    if dates:
        params["dates"] = dates
    resp = requests.get(f"{base}/scoreboard", params=params,
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    season_type_filter = (competition.get("event_filter") or {}).get("season_type")
    out = []
    for ev in data.get("events", []):
        if season_type_filter is not None and ev.get("season", {}).get("type", 0) != season_type_filter:
            continue
        comp = ev["competitions"][0]
        status = comp["status"]["type"]
        home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
        away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
        home_score = int(home["score"]) if str(home.get("score", "")).isdigit() else 0
        away_score = int(away["score"]) if str(away.get("score", "")).isdigit() else 0
        completed = bool(status.get("completed"))
        # A completed match with no declared winner and equal score is a draw
        # (knockouts always resolve a winner via ET/penalties).
        no_winner = not home.get("winner") and not away.get("winner")
        is_draw = completed and no_winner and home_score == away_score
        out.append({
            "espn_game_id": ev["id"],
            "home_team_id": int(home["team"]["id"]),
            "away_team_id": int(away["team"]["id"]),
            "home_team_abbr": home["team"].get("abbreviation", "?"),
            "home_team_name": home["team"].get("displayName", "?"),
            "away_team_abbr": away["team"].get("abbreviation", "?"),
            "away_team_name": away["team"].get("displayName", "?"),
            "home_score": home_score,
            "away_score": away_score,
            "is_complete": completed,
            "state": status.get("state", "pre"),
            "status_detail": status.get("shortDetail", ""),
            "stage": resolve_stage(competition["league"], ev.get("season", {}).get("slug", "")),
            "is_draw": is_draw,
        })
    return out


def fetch_group_winners(competition):
    """Return the set of ext_ids that are ranked 1st in their group. Empty set
    before the group stage finishes or if standings are unavailable."""
    url = (f"https://site.api.espn.com/apis/v2/sports/"
           f"{competition['espn_sport']}/{competition['espn_slug']}/standings")
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return set()
    winners = set()
    for group in data.get("children", []):
        for entry in group.get("standings", {}).get("entries", []):
            stats = {s["name"]: s.get("value", s.get("displayValue")) for s in entry.get("stats", [])}
            try:
                rank = int(stats.get("rank"))
            except (TypeError, ValueError):
                continue
            if rank == 1:
                winners.add(int(entry["team"]["id"]))
    return winners


def fetch_nhl_standings(n=16):
    """Fetch current NHL season standings and return only clinched playoff teams."""
    url = "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    PLAYOFF_CLINCH = {"y", "z", "x", "*"}  # clinched playoff spot (not play-in)

    playoff_teams = []
    for child in data.get("children", []):
        conf = child.get("name", "")
        for entry in child.get("standings", {}).get("entries", []):
            team = entry.get("team", {})
            stats = {s["name"]: s["displayValue"] for s in entry.get("stats", [])}
            clinch = stats.get("clincher", "")
            if clinch not in PLAYOFF_CLINCH:
                continue
            seed = int(stats.get("playoffSeed", 99))
            wl = stats.get("overall", "0-0-0").split("-")
            wins = int(wl[0])
            losses = int(wl[1]) if len(wl) > 1 else 0
            playoff_teams.append({
                "id": int(team["id"]),
                "name": team.get("displayName", ""),
                "abbreviation": team.get("abbreviation", ""),
                "conference": "East" if "East" in conf else "West",
                "seed": seed,
                "wins": wins,
                "losses": losses,
            })

    playoff_teams.sort(key=lambda x: (x["conference"], x["seed"]))
    return playoff_teams[:n]
