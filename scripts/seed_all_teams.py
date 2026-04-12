"""
Seed both NBA and NHL playoff teams from ESPN standings.

Usage: python scripts/seed_all_teams.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from services.espn_api import fetch_nhl_standings
from services.supabase_client import get_service_client
import requests

TOP_N_TEAMS = 16  # Top 16 per league


def fetch_nba_standings(n=20):
    """Fetch current NBA season standings and return top N teams by wins."""
    url = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    all_teams = []
    for child in data.get("children", []):
        conf = child.get("name", "")
        for entry in child.get("standings", {}).get("entries", []):
            team = entry.get("team", {})
            stats = {s["name"]: s["displayValue"] for s in entry.get("stats", [])}
            wl = stats.get("overall", "0-0").split("-")
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

    for conf in ["East", "West"]:
        conf_teams = [t for t in top if t["conference"] == conf]
        for i, t in enumerate(conf_teams, 1):
            t["seed"] = i

    return top


def seed_nba():
    print(f"\n=== NBA: Fetching top {TOP_N_TEAMS} teams ===")
    teams = fetch_nba_standings(TOP_N_TEAMS)
    sb = get_service_client()

    for team in teams:
        sb.table("nba_teams").upsert({
            "id": team["id"],
            "name": team["name"],
            "abbreviation": team["abbreviation"],
            "conference": team["conference"],
            "seed": team.get("seed"),
            "is_eliminated": False,
            "playoff_wins": 0,
            "playoff_losses": 0,
        }).execute()
        print(f"  {team['abbreviation']:4s} {team['name']:30s} ({team['wins']}-{team['losses']}) {team['conference']} #{team.get('seed', '?')}")

    print(f"Seeded {len(teams)} NBA teams.")
    return teams


def seed_nhl():
    print(f"\n=== NHL: Fetching top {TOP_N_TEAMS} teams ===")
    teams = fetch_nhl_standings(TOP_N_TEAMS)
    sb = get_service_client()

    for team in teams:
        sb.table("nhl_teams").upsert({
            "id": team["id"],
            "name": team["name"],
            "abbreviation": team["abbreviation"],
            "conference": team["conference"],
            "seed": team.get("seed"),
            "is_eliminated": False,
            "playoff_wins": 0,
            "playoff_losses": 0,
        }).execute()
        print(f"  {team['abbreviation']:4s} {team['name']:30s} ({team['wins']}-{team['losses']}) {team['conference']} #{team.get('seed', '?')}")

    print(f"Seeded {len(teams)} NHL teams.")
    return teams


if __name__ == "__main__":
    nba = seed_nba()
    nhl = seed_nhl()
    print(f"\nDone! {len(nba)} NBA + {len(nhl)} NHL = {len(nba)+len(nhl)} total teams.")
