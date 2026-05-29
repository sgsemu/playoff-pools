import time
import datetime
from flask import Blueprint, render_template, jsonify, session, redirect, flash
from routes.auth import login_required
from services.supabase_client import get_service_client
from services.scoring import calculate_team_scores, calculate_salary_cap_scores
from services.espn_api import fetch_upcoming_games, fetch_scoreboard, fetch_nhl_scoreboard, fetch_live_games, fetch_calendar_games, today_et, fetch_group_winners
from services.quotes import quote_of_the_day
from services.team_colors import team_color


def playoff_day_count():
    """Day N of playoffs, measured from the earliest completed game in the DB.
    Returns 0 if no games have been synced yet."""
    sb = get_service_client()
    rows = sb.table("game_results").select("game_date").order("game_date").limit(1).execute().data
    if not rows:
        return 0
    start = datetime.date.fromisoformat(rows[0]["game_date"])
    return max(1, (today_et() - start).days + 1)

scores_bp = Blueprint("scores", __name__)


@scores_bp.route("/pool/<pool_id>/scores")
@login_required
def game_scores(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404
    pool = pool[0]

    standings, member_teams = build_standings_view(pool_id)
    calendar = fetch_calendar_games(days_back=7, days_forward=7)
    active_date = _active_calendar_date(calendar)

    return render_template("pool/scores.html",
        pool=pool, standings=standings, member_teams=member_teams,
        calendar=calendar, active_date=active_date,
        playoff_day=playoff_day_count(), quote=quote_of_the_day())


def _active_calendar_date(calendar):
    """Default tab: today if it has games, otherwise the nearest day with games
    (prefer the most recent past day, then the next upcoming day)."""
    today_key = today_et().isoformat()
    if today_key in calendar:
        return today_key
    past = [k for k in calendar if k < today_key]
    future = [k for k in calendar if k > today_key]
    if past:
        return past[-1]
    if future:
        return future[0]
    return None


@scores_bp.route("/pool/<pool_id>/standings.partial")
@login_required
def standings_partial(pool_id):
    # Piggyback an ESPN sync on the standings poll, throttled across viewers
    # so we don't hammer ESPN or Supabase.
    maybe_auto_sync(throttle_seconds=120)
    standings, member_teams = build_standings_view(pool_id)
    return render_template("pool/_standings_table.html",
        standings=standings, member_teams=member_teams)


@scores_bp.route("/pool/<pool_id>/calendar.partial")
@login_required
def calendar_partial(pool_id):
    maybe_auto_sync(throttle_seconds=120)
    calendar = fetch_calendar_games(days_back=7, days_forward=7)
    active_date = _active_calendar_date(calendar)
    return render_template("pool/_calendar.html",
        calendar=calendar, active_date=active_date)


@scores_bp.route("/pool/<pool_id>/scores/live.json")
@login_required
def live_scores_json(pool_id):
    return jsonify({"live": fetch_live_games()})


def _sync_completed_games():
    """Sync completed games for every competition linked to a pool. Returns the
    count of newly inserted game_results rows."""
    from services.sync import sync_competition_results, competitions_for_active_pools
    sb = get_service_client()
    total = 0
    for comp in competitions_for_active_pools(sb):
        total += sync_competition_results(sb, comp)
    return total


# Process-level throttle so polling doesn't hammer ESPN. Vercel Fluid Compute
# reuses function instances, so many concurrent requests share this state.
# Cold starts reset it, which is acceptable — a fresh sync on the first hit
# after a cold start is exactly what we want.
_last_auto_sync_at = 0.0


def maybe_auto_sync(throttle_seconds=120):
    """Run ESPN sync at most once per throttle window. Recalcs standings for
    pools with completed drafts when new games land. Returns new_count."""
    global _last_auto_sync_at
    now = time.time()
    if now - _last_auto_sync_at < throttle_seconds:
        return 0
    _last_auto_sync_at = now
    try:
        new_count = _sync_completed_games()
    except Exception:
        return 0
    if new_count > 0:
        sb = get_service_client()
        for p in sb.table("pools").select("id").eq("draft_status", "complete").execute().data:
            try:
                recalculate_standings(p["id"])
            except Exception:
                pass
    return new_count


@scores_bp.route("/pool/<pool_id>/scores/refresh", methods=["POST"])
@login_required
def refresh_scores(pool_id):
    """Manually sync latest game results from ESPN and recalculate standings."""
    sb = get_service_client()
    pool = sb.table("pools").select("id").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404

    new_count = _sync_completed_games()
    if new_count > 0:
        recalculate_standings(pool_id)
        flash(f"Synced {new_count} new game(s) and updated standings.", "success")
    else:
        flash("Scores are up to date — no new completed games.", "success")

    return redirect(f"/pool/{pool_id}/scores")


def build_standings_view(pool_id):
    """Compute the tie-aware standings and per-member team rosters with live
    win counts. Shared by the pool home, scores page, and JSON refresh."""
    sb = get_service_client()
    members = sb.table("pool_members").select("id,user_id").eq(
        "pool_id", pool_id
    ).execute().data
    user_ids = [m["user_id"] for m in members]
    names = {}
    if user_ids:
        for u in sb.table("users").select("id,display_name").in_(
            "id", user_ids
        ).execute().data:
            names[u["id"]] = u.get("display_name") or "?"

    db_standings = {
        s["member_id"]: s
        for s in sb.table("pool_standings").select("member_id,total_points").eq(
            "pool_id", pool_id
        ).execute().data
    }

    all_games = sb.table("game_results").select("*").execute().data
    team_wins = {}
    for g in all_games:
        cid = g.get("competition_id")
        winner_ext = g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
        if g["home_score"] != g["away_score"]:
            team_wins[(cid, winner_ext)] = team_wins.get((cid, winner_ext), 0) + 1

    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order").execute().data
    from services.competitions import teams_by_ref
    team_lookup = teams_by_ref(sb, [p["team_ref"] for p in picks if p.get("team_ref")])
    member_teams = {}
    for p in picks:
        t = team_lookup.get(p.get("team_ref"))
        if not t:
            continue
        member_teams.setdefault(p["member_id"], []).append({
            "league": t.get("league", ""),
            "abbreviation": t["abbreviation"],
            "name": t["name"],
            "wins": team_wins.get((t["competition_id"], t["ext_id"]), 0),
            "color": (t.get("color") or "").lstrip("#") or team_color(t.get("league", ""), t["ext_id"]),
        })

    rows = [{
        "member_id": m["id"],
        "display_name": names.get(m["user_id"], "?"),
        "total_points": (db_standings.get(m["id"], {}).get("total_points") or 0),
    } for m in members]
    rows.sort(key=lambda r: (-r["total_points"], r["display_name"].lower()))

    prev_points = object()
    prev_rank = 0
    standings = []
    for i, r in enumerate(rows, 1):
        rank = prev_rank if r["total_points"] == prev_points else i
        prev_rank, prev_points = rank, r["total_points"]
        standings.append({**r, "rank": rank})

    return standings, member_teams


def recalculate_standings(pool_id):
    """Recalculate and write standings for a pool. Called after game data sync."""
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data[0]
    members = sb.table("pool_members").select("*").eq("pool_id", pool_id).execute().data

    if pool.get("scoring_config", {}).get("type") == "stage_weighted":
        from services.competitions import get_pool_competition_ids, teams_by_ref
        from services.scoring import calculate_stage_weighted_scores

        comp_ids = get_pool_competition_ids(sb, pool_id)
        comps = sb.table("competitions").select("*").in_("id", comp_ids).execute().data if comp_ids else []
        comp = comps[0] if comps else {}
        stages = comp.get("stages", [])

        picks = sb.table("draft_picks").select("*").eq("pool_id", pool_id).execute().data
        team_lookup = teams_by_ref(sb, [p["team_ref"] for p in picks if p.get("team_ref")])
        ext_by_member = {}
        for p in picks:
            t = team_lookup.get(p.get("team_ref"))
            if t:
                ext_by_member.setdefault(p["member_id"], []).append(t["ext_id"])
        member_teams = ext_by_member

        # Per-team results from game_results, keyed by ext_id, with stage + outcome.
        games = sb.table("game_results").select("*").eq("competition_id", comp.get("id")).execute().data
        team_results = {}
        for g in games:
            stage = g.get("stage")
            if g.get("is_draw"):
                team_results.setdefault(g["home_team_id"], []).append((stage, "draw"))
                team_results.setdefault(g["away_team_id"], []).append((stage, "draw"))
            else:
                winner = g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
                loser = g["away_team_id"] if winner == g["home_team_id"] else g["home_team_id"]
                team_results.setdefault(winner, []).append((stage, "win"))
                team_results.setdefault(loser, []).append((stage, "loss"))

        group_winners = fetch_group_winners(comp) if comp else set()
        scores = calculate_stage_weighted_scores(stages, team_results, member_teams, group_winners)

    elif pool["type"] in ("draft", "auction"):
        # Build team_wins from game_results. Key by (league, team_id) because
        # NBA and NHL ESPN ids overlap.
        games = sb.table("game_results").select("*").execute().data
        team_wins = {}
        for g in games:
            league = g.get("league", "nba")
            winner_id = g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
            key = (league, winner_id)
            team_wins[key] = team_wins.get(key, 0) + 1

        # Series wins: check if any team has 4 wins in a round (simplified)
        series_wins = {}  # round -> [team_ids]
        # TODO: proper series tracking would need game grouping by round/series

        # Build member_teams from draft_picks or auction_bids. Values are
        # (league, team_id) tuples so lookups match team_wins keys.
        if pool["type"] == "draft":
            picks = sb.table("draft_picks").select("*").eq("pool_id", pool_id).execute().data
            member_teams = {}
            for p in picks:
                tid = p.get("team_id") or p.get("nba_team_id")
                league = p.get("league", "nba")
                member_teams.setdefault(p["member_id"], []).append((league, tid))
        else:
            bids = sb.table("auction_bids").select("*").eq(
                "pool_id", pool_id
            ).eq("is_winning_bid", True).execute().data
            member_teams = {}
            for b in bids:
                tid = b.get("team_id") or b.get("nba_team_id")
                league = b.get("league", "nba")
                member_teams.setdefault(b["member_id"], []).append((league, tid))

        scores = calculate_team_scores(pool["scoring_config"], team_wins, member_teams, series_wins)

    elif pool["type"] == "salary_cap":
        rosters = sb.table("salary_rosters").select("*").eq("pool_id", pool_id).execute().data
        member_players = {}
        for r in rosters:
            member_players.setdefault(r["member_id"], []).append(r["nba_player_id"])

        players_data = sb.table("nba_players").select("*").execute().data
        player_stats = {
            p["id"]: {
                "points": p["playoff_points"],
                "rebounds": p["playoff_rebounds"],
                "assists": p["playoff_assists"]
            } for p in players_data
        }

        scores = calculate_salary_cap_scores(pool["scoring_config"], member_players, player_stats)
    else:
        scores = {}

    # Sort by score descending, with display name as a deterministic
    # tiebreaker so ties render in a stable order.
    names = {}
    for m in members:
        u = sb.table("users").select("display_name").eq("id", m["user_id"]).execute().data
        names[m["id"]] = (u[0]["display_name"] if u else "").lower()
    sorted_members = sorted(
        scores.items(),
        key=lambda x: (-x[1], names.get(x[0], "")),
    )

    # Standard competition ranking: tied scores share the lower rank
    # (1,1,1,4,...), so the next distinct score skips ahead by the tie size.
    prev_score = object()
    prev_rank = 0
    for i, (member_id, total) in enumerate(sorted_members, 1):
        rank = prev_rank if total == prev_score else i
        prev_rank, prev_score = rank, total
        sb.table("pool_standings").upsert({
            "pool_id": pool_id,
            "member_id": member_id,
            "rank": rank,
            "total_points": total,
            "points_breakdown": {},
        }, on_conflict="pool_id,member_id").execute()

        sb.table("pool_members").update({"total_points": total}).eq(
            "id", member_id
        ).execute()
