"""
Pre-playoff data seeder: fetches NBA playoff teams and rosters from ESPN,
generates salary values, and inserts into Supabase.

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


def seed_teams():
    print("Fetching playoff teams from ESPN...")
    teams = fetch_playoff_teams()
    sb = get_service_client()

    # For MVP, we take the top 16 teams (8 per conference) as playoff teams
    # ESPN doesn't distinguish playoff teams in the teams endpoint,
    # so we'll seed all teams and mark playoff status manually or via standings
    for team in teams:
        sb.table("nba_teams").upsert({
            "id": team["id"],
            "name": team["name"],
            "abbreviation": team["abbreviation"],
            "conference": team["conference"],
            "seed": None,  # Set manually or via standings endpoint
            "is_eliminated": False,
            "playoff_wins": 0,
            "playoff_losses": 0,
        }).execute()

    print(f"Seeded {len(teams)} teams.")
    return teams


def seed_players_and_salaries(teams):
    sb = get_service_client()
    all_players = []

    print("Fetching rosters...")
    for team in teams:
        roster = fetch_team_roster(team["id"])
        for p in roster:
            all_players.append({
                "id": p["id"],
                "name": p["name"],
                "team_id": p["team_id"],
                "position": p["position"],
                "ppg": 0,  # Will be populated from stats
                "rpg": 0,
                "apg": 0,
            })

    # For salary generation, we need season stats.
    # For now, seed players with zero stats; update via a separate stats fetch.
    print(f"Computing salaries for {len(all_players)} players...")
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

    print(f"Seeded {len(all_players)} players with salaries.")


if __name__ == "__main__":
    teams = seed_teams()
    seed_players_and_salaries(teams)
    print("Done!")
