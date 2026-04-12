"""
Seed both NBA and NHL playoff teams from ESPN standings.
Only seeds teams that have clinched a playoff spot.

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

PLAYOFF_CLINCH = {"y", "z", "x", "*"}  # clinched playoff spot (not play-in)


def fetch_nba_playoff_teams():
    """Fetch NBA teams that have clinched a playoff spot."""
    url = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

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
            wl = stats.get("overall", "0-0").split("-")
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
    return playoff_teams


def seed_nba():
    print("\n=== NBA Playoff Teams ===")
    teams = fetch_nba_playoff_teams()
    sb = get_service_client()

    for team in teams:
        sb.table("nba_teams").upsert({
            "id": team["id"],
            "name": team["name"],
            "abbreviation": team["abbreviation"],
            "conference": team["conference"],
            "seed": team["seed"],
            "is_eliminated": False,
            "playoff_wins": 0,
            "playoff_losses": 0,
        }).execute()
        print(f"  #{team['seed']:<2d} {team['abbreviation']:4s} {team['name']:30s} ({team['wins']}-{team['losses']}) {team['conference']}")

    print(f"Seeded {len(teams)} NBA playoff teams.")
    return teams


def seed_nhl():
    print("\n=== NHL Playoff Teams ===")
    teams = fetch_nhl_standings(16)
    sb = get_service_client()

    for team in teams:
        sb.table("nhl_teams").upsert({
            "id": team["id"],
            "name": team["name"],
            "abbreviation": team["abbreviation"],
            "conference": team["conference"],
            "seed": team["seed"],
            "is_eliminated": False,
            "playoff_wins": 0,
            "playoff_losses": 0,
        }).execute()
        print(f"  #{team['seed']:<2d} {team['abbreviation']:4s} {team['name']:30s} ({team['wins']}-{team['losses']}) {team['conference']}")

    print(f"Seeded {len(teams)} NHL playoff teams.")
    return teams


if __name__ == "__main__":
    nba = seed_nba()
    nhl = seed_nhl()
    print(f"\nDone! {len(nba)} NBA + {len(nhl)} NHL = {len(nba)+len(nhl)} playoff teams.")
