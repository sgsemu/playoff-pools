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
from services.sync import sync_competition_results, competitions_for_active_pools
from routes.scores import recalculate_standings

app = Flask(__name__)


@app.route("/api/cron/sync-games", methods=["GET"])
def sync_games():
    sb = get_service_client()
    total_new = 0
    for comp in competitions_for_active_pools(sb):
        total_new += sync_competition_results(sb, comp)
    if total_new > 0:
        for pool in sb.table("pools").select("id").eq("draft_status", "complete").execute().data:
            recalculate_standings(pool["id"])
    return jsonify({"synced": total_new})
