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

from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, jsonify
from services.supabase_client import get_service_client
from services.sync import sync_competition_results, competitions_for_active_pools
from services.survivor_data import resolve_and_apply
from routes.scores import recalculate_standings
from services import odds as odds_service

app = Flask(__name__)

_ET = ZoneInfo("America/New_York")
_PROP_WEEKDAYS = {"Thu", "Fri", "Sat", "Sun"}


def _now_et():
    return datetime.now(_ET)


def _current_week_odds_event_ids(sb, nfl_comp_ids):
    """Odds API event ids for the current NFL week's games, resolved from
    the cached lines via get_event_for_game() (a cache-only read -- see
    services/odds.py -- so this costs nothing against the credit governor).
    "Current week" is deliberately simple here (not the lock-aware logic
    survivor's _resolve_current_week uses): the highest week number that
    still has an incomplete game, or the highest week present if every game
    already reported complete. Returns [] if there's no NFL competition or
    no game data yet."""
    if not nfl_comp_ids:
        return []
    games_rows = sb.table("game_results").select(
        "week, is_complete, home_team_id, away_team_id"
    ).in_("competition_id", nfl_comp_ids).execute().data
    weeks = sorted({r["week"] for r in games_rows if r.get("week") is not None})
    if not weeks:
        return []
    incomplete_weeks = [
        w for w in weeks
        if any(r["week"] == w and not r.get("is_complete") for r in games_rows)
    ]
    current_week = max(incomplete_weeks) if incomplete_weeks else max(weeks)

    teams_rows = sb.table("teams").select("*").in_(
        "competition_id", nfl_comp_ids
    ).execute().data
    teams_by_ext = {t["ext_id"]: t for t in teams_rows}

    event_ids = []
    for g in games_rows:
        if g.get("week") != current_week:
            continue
        home = teams_by_ext.get(g.get("home_team_id"))
        away = teams_by_ext.get(g.get("away_team_id"))
        if not home or not away:
            continue
        ev = odds_service.get_event_for_game({
            "league": "nfl",
            "home": {"name": home.get("name")},
            "away": {"name": away.get("name")},
        })
        if ev and ev.get("id"):
            event_ids.append(ev["id"])
    return event_ids


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

    # Player-prop odds (anytime TD), Thu-Sun only and governor-gated: props
    # cost one credit PER EVENT (not one per league like the lines refresh
    # above), so this is deliberately scoped to NFL's current week and to
    # the days games are actually played, to protect the free-tier credit
    # budget. can_refresh(60) is re-checked before every individual call
    # inside refresh_odds_props() too -- this outer check is just a cheap
    # skip when the floor is already known to be hit. Same broad except as
    # above: a props failure must never break games-sync/lines-refresh.
    props_refreshed = 0
    try:
        if _now_et().strftime("%a") in _PROP_WEEKDAYS and odds_service.can_refresh(60):
            nfl_comp_ids = [c["id"] for c in active_comps if c.get("league") == "nfl"]
            event_ids = _current_week_odds_event_ids(sb, nfl_comp_ids)
            if event_ids:
                odds_service.refresh_odds_props("nfl", event_ids)
                props_refreshed = len(event_ids)
    except Exception as exc:
        print(f"[cron] props refresh block failed: {exc}")

    return jsonify({
        "synced": total_new,
        "odds_refreshed": odds_refreshed,
        "props_refreshed": props_refreshed,
    })
