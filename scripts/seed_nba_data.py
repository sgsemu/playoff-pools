"""
Pre-playoff data seeder: fetches NBA playoff teams and rosters from ESPN,
generates salary values based on PPG, and inserts into Supabase.

Usage: python scripts/seed_nba_data.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from services.espn_api import fetch_playoff_teams, fetch_team_roster
from services.salary_generator import compute_salaries
from services.supabase_client import get_service_client
import requests

# 2026 NBA playoff team IDs — update these each year
# These are the 16 teams that made the playoffs
PLAYOFF_TEAM_IDS = None  # Set to None to seed all teams, or a list of IDs to filter


def fetch_player_ppg(player_id):
    """Fetch a player's current season PPG from ESPN."""
    try:
        url = f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{player_id}/stats"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return 0.0
        data = resp.json()
        for cat in data.get("categories", []):
            if cat.get("name") == "averages":
                labels = cat.get("labels", [])
                if "PTS" not in labels:
                    continue
                pts_idx = labels.index("PTS")
                # Get the most recent season stats
                stats_list = cat.get("statistics", [])
                if stats_list:
                    last_season = stats_list[-1]
                    vals = last_season.get("stats", [])
                    if len(vals) > pts_idx:
                        return float(vals[pts_idx])
                break
        return 0.0
    except Exception:
        return 0.0


def seed_teams():
    print("Fetching teams from ESPN...")
    teams = fetch_playoff_teams()
    sb = get_service_client()

    if PLAYOFF_TEAM_IDS:
        teams = [t for t in teams if t["id"] in PLAYOFF_TEAM_IDS]

    for team in teams:
        sb.table("nba_teams").upsert({
            "id": team["id"],
            "name": team["name"],
            "abbreviation": team["abbreviation"],
            "conference": team["conference"],
            "seed": None,
            "is_eliminated": False,
            "playoff_wins": 0,
            "playoff_losses": 0,
        }).execute()

    print(f"Seeded {len(teams)} teams.")
    return teams


def seed_players_and_salaries(teams):
    sb = get_service_client()
    all_players = []

    print("Fetching rosters and PPG stats (this may take a few minutes)...")
    for team in teams:
        print(f"  {team['abbreviation']}...", end=" ", flush=True)
        roster = fetch_team_roster(team["id"])
        for p in roster:
            # Fetch each player's PPG
            ppg = fetch_player_ppg(p["id"])
            all_players.append({
                "id": p["id"],
                "name": p["name"],
                "team_id": p["team_id"],
                "position": p["position"],
                "ppg": ppg,
            })
        print(f"{len(roster)} players")

    print(f"\nComputing salaries for {len(all_players)} players based on PPG...")
    salaries = compute_salaries(all_players, cap=50000)

    for p in all_players:
        sb.table("nba_players").upsert({
            "id": p["id"],
            "name": p["name"],
            "team_id": p["team_id"],
            "position": p["position"],
            "salary_value": salaries.get(p["id"], 500),
            "playoff_points": 0,
            "playoff_rebounds": 0,
            "playoff_assists": 0,
        }).execute()

    # Print top 10 salaries for verification
    top = sorted(all_players, key=lambda x: x["ppg"], reverse=True)[:10]
    print("\nTop 10 by PPG:")
    for p in top:
        print(f"  {p['name']:25s} PPG: {p['ppg']:5.1f}  Salary: ${salaries.get(p['id'], 0):,}")

    print(f"\nSeeded {len(all_players)} players with PPG-based salaries.")


if __name__ == "__main__":
    teams = seed_teams()
    seed_players_and_salaries(teams)
    print("Done!")
