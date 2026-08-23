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
from services import odds as odds_service

app = Flask(__name__)


@app.route("/api/cron/sync-games", methods=["GET"])
def sync_games():
    sb = get_service_client()
    total_new = 0
    active_comps = competitions_for_active_pools(sb)
    for comp in active_comps:
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

    # Refresh The Odds API lines once daily, for every league with an active
    # competition AND an Odds API sport_key mapping. Cache-only reads
    # (fetch_odds/enrich_calendar_with_best_odds) never call the upstream
    # API themselves (see services/odds.py module docstring) -- this cron is
    # the ONLY place lines get refreshed on Hobby (no other scheduled
    # trigger is available). Gated by the credit governor so a busy day
    # never drains the free-tier monthly quota; an odds failure here must
    # never break games-sync/survivor-resolve above, hence the broad except.
    odds_refreshed = 0
    try:
        leagues = {
            comp.get("league") for comp in active_comps
            if odds_service.sport_key(comp.get("league"))
        }
        for league in leagues:
            if odds_service.can_refresh(60):
                odds_service.refresh_odds_lines(league)
                odds_refreshed += 1
            else:
                print(f"[cron] odds refresh skipped for league={league!r}: credit governor floor hit")
    except Exception as exc:
        print(f"[cron] odds refresh block failed: {exc}")

    return jsonify({"synced": total_new, "odds_refreshed": odds_refreshed})
