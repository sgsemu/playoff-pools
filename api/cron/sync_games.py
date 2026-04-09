# api/cron/sync_games.py
"""
Vercel Cron job: polls ESPN for game results, updates database, recalculates standings.
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
from services.espn_api import fetch_scoreboard, fetch_game_boxscore
from routes.scores import recalculate_standings

app = Flask(__name__)


@app.route("/api/cron/sync-games", methods=["GET"])
def sync_games():
    sb = get_service_client()

    # Fetch today's scoreboard
    games = fetch_scoreboard()

    new_results = 0
    for game in games:
        if not game["is_complete"]:
            continue

        # Check if already stored
        existing = sb.table("game_results").select("id").eq(
            "espn_game_id", game["espn_game_id"]
        ).execute().data

        if existing:
            continue

        # Determine round (simplified: would need series data from ESPN)
        sb.table("game_results").insert({
            "espn_game_id": game["espn_game_id"],
            "home_team_id": game["home_team_id"],
            "away_team_id": game["away_team_id"],
            "home_score": game["home_score"],
            "away_score": game["away_score"],
            "round": 1,  # TODO: determine from ESPN series data
            "game_date": __import__("datetime").date.today().isoformat(),
        }).execute()

        # Update team records
        winner_id = game["home_team_id"] if game["home_score"] > game["away_score"] else game["away_team_id"]
        loser_id = game["away_team_id"] if winner_id == game["home_team_id"] else game["home_team_id"]

        sb.rpc("increment_wins", {"team_id": winner_id}).execute()
        sb.rpc("increment_losses", {"team_id": loser_id}).execute()

        # Fetch and update player stats
        box_score = fetch_game_boxscore(game["espn_game_id"])
        for player in box_score:
            sb.table("nba_players").update({
                "playoff_points": player["points"],
                "playoff_rebounds": player["rebounds"],
                "playoff_assists": player["assists"],
            }).eq("id", player["espn_player_id"]).execute()

        new_results += 1

    # Recalculate standings for all active pools
    if new_results > 0:
        pools = sb.table("pools").select("id").eq("draft_status", "active").execute().data
        for pool in pools:
            recalculate_standings(pool["id"])

    return jsonify({"synced": new_results})
