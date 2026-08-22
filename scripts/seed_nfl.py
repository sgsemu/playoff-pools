# scripts/seed_nfl.py
"""Seed the NFL 2026 regular-season competition + 32 teams from ESPN.
Run once: python -m scripts.seed_nfl"""
import sys, requests
from services.supabase_client import get_service_client

_HDRS = {"User-Agent": "Mozilla/5.0"}
ESPN_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
# Fallback host: site.api occasionally 403s a given IP (rate limiting); the
# core API is a separate host that returns the same teams as $ref links.
ESPN_CORE_TEAMS = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/teams?limit=40"


def _fetch_teams_site():
    r = requests.get(ESPN_TEAMS, headers=_HDRS, timeout=15)
    r.raise_for_status()
    out = []
    for t in r.json().get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        team = t["team"]
        out.append({
            "ext_id": int(team["id"]),
            "name": team["displayName"],
            "abbreviation": team["abbreviation"],
        })
    return out


def _fetch_teams_core():
    """Fallback: list team $refs from the core API, then fetch each for details."""
    r = requests.get(ESPN_CORE_TEAMS, headers=_HDRS, timeout=15)
    r.raise_for_status()
    out = []
    for item in r.json().get("items", []):
        ref = item.get("$ref")
        if not ref:
            continue
        d = requests.get(ref.replace("http://", "https://"), headers=_HDRS, timeout=15).json()
        out.append({
            "ext_id": int(d["id"]),
            "name": d["displayName"],
            "abbreviation": d["abbreviation"],
        })
    return out


def fetch_teams():
    try:
        return _fetch_teams_site()
    except Exception as e:
        print(f"site.api teams failed ({e}); falling back to core API", file=sys.stderr)
        return _fetch_teams_core()


def main():
    sb = get_service_client()
    existing = sb.table("competitions").select("id").eq("league", "nfl").eq("season", 2026).execute().data
    if existing:
        comp_id = existing[0]["id"]
        print(f"NFL 2026 already exists: {comp_id}")
    else:
        comp = sb.table("competitions").insert({
            "league": "nfl", "season": 2026, "name": "NFL 2026",
            "espn_sport": "football", "espn_slug": "nfl",
            "event_filter": {"season_type": 2}, "stages": [],
            "scoring_defaults": {"type": "survivor"}, "status": "active",
        }).execute().data[0]
        comp_id = comp["id"]
        print(f"Created competition {comp_id}")
    teams = fetch_teams()
    if len(teams) != 32:
        print(f"WARNING: expected 32 teams, got {len(teams)}", file=sys.stderr)
    for t in teams:
        sb.table("teams").upsert({"competition_id": comp_id, "grouping": None, "seed": None, **t},
                                 on_conflict="competition_id,ext_id").execute()
    print(f"Upserted {len(teams)} teams")


if __name__ == "__main__":
    main()
