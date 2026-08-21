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
from services.survivor_data import resolve_and_apply
from routes.scores import recalculate_standings

app = Flask(__name__)


@app.route("/api/cron/sync-games", methods=["GET"])
def sync_games():
    sb = get_service_client()
    total_new = 0
    for comp in competitions_for_active_pools(sb):
        total_new += sync_competition_results(sb, comp)
    if total_new > 0:
        # Survivor pools have no draft phase, so they're eligible for
        # resolution regardless of draft_status -- only draft/auction/
        # salary_cap pools need draft_status=='complete' before recalculating
        # standings (see task-10 finding: filtering all pools by
        # draft_status=='complete' made survivor auto-resolution unreachable
        # during the live season, since survivor pools default to 'pending'
        # and only flip to 'complete' via settle_season at season end).
        for pool in sb.table("pools").select("*").execute().data:
            if pool.get("type") == "survivor":
                resolve_and_apply(sb, pool)
            elif pool.get("draft_status") == "complete":
                recalculate_standings(pool["id"])
    return jsonify({"synced": total_new})
