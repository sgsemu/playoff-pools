"""Single competition-aware ingestion path, used by both the Vercel cron and
the runtime auto-sync. Writes game_results tagged with competition_id/stage/
is_draw, and keeps the legacy league/round columns so the existing NBA/NHL
scoring path is unaffected."""
import datetime
from services.espn_api import fetch_competition_results, today_et


def competitions_for_active_pools(sb):
    """Distinct competition rows linked to pools whose draft isn't complete-less
    — i.e. any pool that still needs live scoring. Returns competition dicts."""
    pools = sb.table("pools").select("id").execute().data
    if not pools:
        return []
    links = sb.table("pool_competitions").select("competition_id").execute().data
    comp_ids = list({l["competition_id"] for l in links})
    if not comp_ids:
        return []
    return sb.table("competitions").select("*").in_("id", comp_ids).eq(
        "status", "active"
    ).execute().data


def sync_competition_results(sb, competition):
    """Fetch + insert any new completed games for one competition. Returns the
    count of newly inserted game_results rows."""
    try:
        games = fetch_competition_results(competition)
    except Exception:
        return 0
    new_count = 0
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
            "competition_id": competition["id"],
            "home_team_id": game["home_team_id"],
            "away_team_id": game["away_team_id"],
            "home_score": game["home_score"],
            "away_score": game["away_score"],
            "stage": game["stage"],
            "is_draw": game["is_draw"],
            "league": competition["league"],   # legacy column (NBA/NHL scoring)
            "round": 1,                          # legacy column, no longer authoritative
            "game_date": today_et().isoformat(),
        }).execute()
        new_count += 1
    return new_count
