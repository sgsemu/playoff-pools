import requests

ESPN_NBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_NHL_BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
ESPN_BASE = ESPN_NBA_BASE  # backward compat


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


def fetch_nhl_standings(n=16):
    """Fetch current NHL season standings and return top N teams by wins."""
    url = "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    all_teams = []
    for child in data.get("children", []):
        conf = child.get("name", "")
        for entry in child.get("standings", {}).get("entries", []):
            team = entry.get("team", {})
            stats = {s["name"]: s["displayValue"] for s in entry.get("stats", [])}
            wl = stats.get("overall", "0-0-0").split("-")
            wins = int(wl[0])
            losses = int(wl[1]) if len(wl) > 1 else 0
            all_teams.append({
                "id": int(team["id"]),
                "name": team.get("displayName", ""),
                "abbreviation": team.get("abbreviation", ""),
                "conference": "East" if "East" in conf else "West",
                "wins": wins,
                "losses": losses,
            })

    all_teams.sort(key=lambda x: x["wins"], reverse=True)
    top = all_teams[:n]

    # Assign overall seed by rank (1-16), not per-conference
    for i, t in enumerate(top, 1):
        t["seed"] = i

    return top
