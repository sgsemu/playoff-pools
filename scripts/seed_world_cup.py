# scripts/seed_world_cup.py
"""Seed the FIFA World Cup 2026 competition + its 48 national teams.

Run once: python -m scripts.seed_world_cup
Idempotent: re-running does not duplicate the competition or teams.
"""
import sys
import requests
from services.supabase_client import get_service_client

ESPN_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/teams"

# Per-win points by stage (see the design spec). Group also scores draws +
# a placement bonus; the Final winner is the champion (5 pts), no extra bonus.
STAGES = [
    {"key": "group",       "label": "Group Stage",         "win_points": 3, "draw_points": 1, "group_winner_bonus": 2},
    {"key": "r32",         "label": "Round of 32",         "win_points": 3},
    {"key": "r16",         "label": "Round of 16",         "win_points": 3},
    {"key": "qf",          "label": "Quarterfinal",        "win_points": 3},
    {"key": "sf",          "label": "Semifinal",           "win_points": 4},
    {"key": "final",       "label": "Final",               "win_points": 5},
    {"key": "third_place", "label": "Third-Place Playoff", "win_points": 3},
]


def fetch_teams():
    resp = requests.get(ESPN_TEAMS, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    leagues = resp.json().get("sports", [{}])[0].get("leagues", [{}])[0]
    out = []
    for entry in leagues.get("teams", []):
        tm = entry["team"]
        out.append({
            "ext_id": int(tm["id"]),
            "name": tm.get("displayName", "?"),
            "abbreviation": tm.get("abbreviation", "?"),
            "color": ("#" + tm["color"]) if tm.get("color") else None,
        })
    return out


def main():
    sb = get_service_client()

    existing = sb.table("competitions").select("id").eq(
        "league", "world_cup"
    ).eq("season", 2026).execute().data
    if existing:
        comp_id = existing[0]["id"]
        print(f"World Cup 2026 competition already exists: {comp_id}")
    else:
        comp = sb.table("competitions").insert({
            "league": "world_cup",
            "season": 2026,
            "name": "FIFA World Cup 2026",
            "espn_sport": "soccer",
            "espn_slug": "fifa.world",
            "event_filter": {"all_tournament": True},
            "stages": STAGES,
            "scoring_defaults": {"type": "stage_weighted"},
            "status": "active",
        }).execute().data[0]
        comp_id = comp["id"]
        print(f"Created competition {comp_id}")

    teams = fetch_teams()
    if len(teams) != 48:
        print(f"WARNING: expected 48 teams, ESPN returned {len(teams)}", file=sys.stderr)

    inserted = 0
    for t in teams:
        row = {"competition_id": comp_id, **t, "grouping": None, "seed": None}
        # upsert on (competition_id, ext_id) so re-runs are safe
        sb.table("teams").upsert(
            row, on_conflict="competition_id,ext_id"
        ).execute()
        inserted += 1
    print(f"Seeded {inserted} teams for World Cup 2026.")


if __name__ == "__main__":
    main()
