"""Single competition-aware ingestion path, used by both the Vercel cron and
the runtime auto-sync. Writes game_results tagged with competition_id/stage/
is_draw, and keeps the legacy league/round columns so the existing NBA/NHL
scoring path is unaffected."""
import datetime
from services.espn_api import (
    _ET, fetch_competition_results, fetch_nfl_week_results_core, today_et,
)


def _game_date(kickoff_at):
    """The game's own date in ET, derived from its kickoff timestamp -- not
    the date sync happens to run on. Falls back to today_et() when
    kickoff_at is missing or unparseable (e.g. a malformed ESPN payload)."""
    if kickoff_at:
        try:
            return datetime.datetime.fromisoformat(
                kickoff_at.replace("Z", "+00:00")
            ).astimezone(_ET).date().isoformat()
        except (ValueError, AttributeError):
            pass
    return today_et().isoformat()


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


def _sync_nfl_results(sb, competition):
    """NFL results via ESPN's CORE api. site.api (which the generic path below
    uses) is 403-blocked from datacenter IPs like Vercel, so the standard sync
    silently fetched nothing and NFL games never flipped to complete -- leaving
    survivor weeks ungraded. The full NFL schedule is loaded up front
    (scripts/load_nfl_schedule.py); this only UPDATES completion/scores/winner
    in place for games whose kickoff has passed but that aren't marked complete
    yet. Returns the newly-completed count so downstream resolve/standings fire
    only on a real false->true transition."""
    season = competition.get("season")
    if season is None:
        return 0
    rows = sb.table("game_results").select(
        "espn_game_id, week, is_complete, kickoff_at"
    ).eq("competition_id", competition["id"]).execute().data
    now = datetime.datetime.now(datetime.timezone.utc)

    def _past(kickoff_at):
        if not kickoff_at:
            return False
        try:
            return datetime.datetime.fromisoformat(kickoff_at.replace("Z", "+00:00")) <= now
        except (ValueError, AttributeError):
            return False

    # Only weeks with a past-kickoff game that still isn't complete need a
    # results fetch -- normally zero (nothing pending) or one (the week that
    # just finished). This keeps the per-event core-API fan-out bounded.
    stale_weeks = sorted({
        r["week"] for r in rows
        if r.get("week") is not None and not r.get("is_complete") and _past(r.get("kickoff_at"))
    })
    if not stale_weeks:
        return 0
    was_complete = {str(r["espn_game_id"]): r.get("is_complete") for r in rows}

    newly_completed = 0
    for week in stale_weeks:
        try:
            games = fetch_nfl_week_results_core(season, week)
        except Exception:
            continue
        for g in games:
            if not g["is_complete"]:
                continue
            gid = str(g["espn_game_id"])
            if not was_complete.get(gid):
                newly_completed += 1
            sb.table("game_results").update({
                "home_score": g["home_score"],
                "away_score": g["away_score"],
                "winner_team_id": g["winner_team_id"],
                "is_draw": g["is_draw"],
                "is_complete": True,
            }).eq("competition_id", competition["id"]).eq("espn_game_id", gid).execute()
    return newly_completed


def sync_competition_results(sb, competition):
    """Fetch + upsert the full game window (schedule + results) for one
    competition, keyed on the UNIQUE espn_game_id -- a scheduled game gets
    inserted once and then updated in place as it kicks off and finishes,
    never duplicated. Returns the count of games that are complete AND newly
    so (brand new and already complete, or a false->true transition since
    the last sync). Pure schedule ingestion -- every game still upcoming --
    always returns 0, which is what keeps standings recalc/survivor
    resolution from firing on a schedule-only sync."""
    # NFL routes to the core API (site.api scoreboard is 403-blocked from
    # datacenter IPs, so the generic path below fetches nothing for NFL).
    if (competition.get("espn_slug") or competition.get("league") or "").lower() == "nfl":
        return _sync_nfl_results(sb, competition)
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
            "game_date": _game_date(game.get("kickoff_at")),
            "is_complete": is_complete,
        }, on_conflict="espn_game_id").execute()
    return newly_completed
