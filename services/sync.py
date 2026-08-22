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
    """Fetch + upsert the full game window (schedule + results) for one
    competition, keyed on the UNIQUE espn_game_id -- a scheduled game gets
    inserted once and then updated in place as it kicks off and finishes,
    never duplicated. Returns the count of games that are complete AND newly
    so (brand new and already complete, or a false->true transition since
    the last sync). Pure schedule ingestion -- every game still upcoming --
    always returns 0, which is what keeps standings recalc/survivor
    resolution from firing on a schedule-only sync."""
    try:
        games = fetch_competition_results(competition)
    except Exception:
        return 0

    # Snapshot prior completion state once, before any upserts, so the
    # newly-completed count reflects transitions during THIS sync only.
    existing_rows = sb.table("game_results").select(
        "espn_game_id,is_complete"
    ).eq("competition_id", competition["id"]).execute().data
    was_complete = {r["espn_game_id"]: r.get("is_complete") for r in existing_rows}

    newly_completed = 0
    for game in games:
        is_complete = game["is_complete"]
        if is_complete and not was_complete.get(game["espn_game_id"]):
            newly_completed += 1
        sb.table("game_results").upsert({
            "espn_game_id": game["espn_game_id"],
            "competition_id": competition["id"],
            "home_team_id": game["home_team_id"],
            "away_team_id": game["away_team_id"],
            "home_score": game["home_score"] if is_complete else 0,
            "away_score": game["away_score"] if is_complete else 0,
            "winner_team_id": game.get("winner_team_id"),
            "stage": game["stage"],
            "is_draw": game["is_draw"],
            "week": game.get("week"),
            "kickoff_at": game.get("kickoff_at"),
            "league": competition["league"],   # legacy column (NBA/NHL scoring)
            "round": 1,                          # legacy column, no longer authoritative
            "game_date": today_et().isoformat(),
            "is_complete": is_complete,
        }, on_conflict="espn_game_id").execute()
    return newly_completed
