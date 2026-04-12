# api/cron/sync_games.py
"""
Vercel Cron job: polls ESPN for NBA + NHL game results, updates database, recalculates standings.
Triggered by vercel.json cron config.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from flask import Flask, jsonify
from services.supabase_client import get_service_client
from services.espn_api import fetch_scoreboard, fetch_nhl_scoreboard
from routes.scores import recalculate_standings

app = Flask(__name__)


def _sync_league_games(sb, games, league, teams_table):
    """Sync games for a single league. Returns count of new results."""
    new_results = 0
    for game in games:
        if not game["is_complete"]:
            continue

        existing = sb.table("game_results").select("id").eq(
            "espn_game_id", game["espn_game_id"]
        ).execute().data

        if existing:
            continue

        sb.table("game_results").insert({
            "espn_game_id": game["espn_game_id"],
            "home_team_id": game["home_team_id"],
            "away_team_id": game["away_team_id"],
            "home_score": game["home_score"],
            "away_score": game["away_score"],
            "round": 1,
            "league": league,
            "game_date": __import__("datetime").date.today().isoformat(),
        }).execute()

        # Update team records
        winner_id = game["home_team_id"] if game["home_score"] > game["away_score"] else game["away_team_id"]
        loser_id = game["away_team_id"] if winner_id == game["home_team_id"] else game["home_team_id"]

        try:
            sb.table(teams_table).update({"playoff_wins": sb.table(teams_table).select("playoff_wins").eq("id", winner_id).execute().data[0]["playoff_wins"] + 1}).eq("id", winner_id).execute()
            sb.table(teams_table).update({"playoff_losses": sb.table(teams_table).select("playoff_losses").eq("id", loser_id).execute().data[0]["playoff_losses"] + 1}).eq("id", loser_id).execute()
        except Exception:
            pass

        new_results += 1

    return new_results


@app.route("/api/cron/sync-games", methods=["GET"])
def sync_games():
    sb = get_service_client()

    # Sync NBA
    nba_games = fetch_scoreboard()
    nba_new = _sync_league_games(sb, nba_games, "nba", "nba_teams")

    # Sync NHL
    nhl_games = fetch_nhl_scoreboard()
    nhl_new = _sync_league_games(sb, nhl_games, "nhl", "nhl_teams")

    total_new = nba_new + nhl_new

    # Recalculate standings for all active pools
    if total_new > 0:
        pools = sb.table("pools").select("id").eq("draft_status", "active").execute().data
        for pool in pools:
            recalculate_standings(pool["id"])

    return jsonify({"synced": total_new, "nba": nba_new, "nhl": nhl_new})
