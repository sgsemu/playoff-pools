from flask import Blueprint, render_template, jsonify, session, redirect, flash
from routes.auth import login_required
from services.supabase_client import get_service_client
from services.scoring import calculate_team_scores, calculate_salary_cap_scores
from services.espn_api import fetch_upcoming_games, fetch_scoreboard, fetch_nhl_scoreboard
import datetime

scores_bp = Blueprint("scores", __name__)


@scores_bp.route("/pool/<pool_id>/scores")
@login_required
def game_scores(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404
    pool = pool[0]

    games = sb.table("game_results").select("*").order("game_date", desc=True).execute().data
    nba_teams = {t["id"]: t for t in sb.table("nba_teams").select("*").execute().data}
    nhl_teams = {t["id"]: t for t in sb.table("nhl_teams").select("*").execute().data}
    all_teams = {**nba_teams, **nhl_teams}

    # Annotate games with team names
    for g in games:
        g["home_name"] = all_teams.get(g["home_team_id"], {}).get("name", "?")
        g["away_name"] = all_teams.get(g["away_team_id"], {}).get("name", "?")

    standings = sb.table("pool_standings").select("*").eq(
        "pool_id", pool_id
    ).order("rank").execute().data

    raw_members = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).execute().data
    for m in raw_members:
        user = sb.table("users").select("display_name").eq("id", m["user_id"]).execute().data
        m["users"] = user[0] if user else {"display_name": "Unknown"}
    member_map = {m["id"]: m for m in raw_members}

    # Build member → teams mapping
    picks = sb.table("draft_picks").select("*").eq("pool_id", pool_id).order("pick_order").execute().data
    member_teams = {}
    for p in picks:
        tid = p.get("team_id") or p.get("nba_team_id")
        league = p.get("league", "nba")
        team = nba_teams.get(tid) if league == "nba" else nhl_teams.get(tid)
        if team:
            member_teams.setdefault(p["member_id"], []).append({
                "name": team["name"],
                "abbreviation": team["abbreviation"],
                "league": league,
                "wins": team.get("playoff_wins", 0),
            })

    upcoming = fetch_upcoming_games(days=7)

    return render_template("pool/scores.html",
        pool=pool, games=games, standings=standings, member_map=member_map,
        member_teams=member_teams, upcoming=upcoming)


@scores_bp.route("/pool/<pool_id>/scores/refresh", methods=["POST"])
@login_required
def refresh_scores(pool_id):
    """Manually sync latest game results from ESPN and recalculate standings."""
    sb = get_service_client()
    pool = sb.table("pools").select("id").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404

    nba_ids = {t["id"] for t in sb.table("nba_teams").select("id").execute().data}
    nhl_ids = {t["id"] for t in sb.table("nhl_teams").select("id").execute().data}
    new_count = 0

    for games, league, team_ids, teams_table in [
        (fetch_scoreboard(), "nba", nba_ids, "nba_teams"),
        (fetch_nhl_scoreboard(), "nhl", nhl_ids, "nhl_teams"),
    ]:
        for game in games:
            if not game["is_complete"]:
                continue
            if game["home_team_id"] not in team_ids or game["away_team_id"] not in team_ids:
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
                "game_date": datetime.date.today().isoformat(),
            }).execute()

            winner_id = game["home_team_id"] if game["home_score"] > game["away_score"] else game["away_team_id"]
            loser_id = game["away_team_id"] if winner_id == game["home_team_id"] else game["home_team_id"]
            try:
                w = sb.table(teams_table).select("playoff_wins").eq("id", winner_id).execute().data
                l = sb.table(teams_table).select("playoff_losses").eq("id", loser_id).execute().data
                if w:
                    sb.table(teams_table).update({"playoff_wins": w[0]["playoff_wins"] + 1}).eq("id", winner_id).execute()
                if l:
                    sb.table(teams_table).update({"playoff_losses": l[0]["playoff_losses"] + 1}).eq("id", loser_id).execute()
            except Exception:
                pass
            new_count += 1

    if new_count > 0:
        recalculate_standings(pool_id)
        flash(f"Synced {new_count} new game(s) and updated standings.", "success")
    else:
        flash("Scores are up to date — no new completed games.", "success")

    return redirect(f"/pool/{pool_id}/scores")


def recalculate_standings(pool_id):
    """Recalculate and write standings for a pool. Called after game data sync."""
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data[0]
    members = sb.table("pool_members").select("*").eq("pool_id", pool_id).execute().data

    if pool["type"] in ("draft", "auction"):
        # Build team_wins and series_wins from game_results
        games = sb.table("game_results").select("*").execute().data
        team_wins = {}
        for g in games:
            winner_id = g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
            team_wins[winner_id] = team_wins.get(winner_id, 0) + 1

        # Series wins: check if any team has 4 wins in a round (simplified)
        series_wins = {}  # round -> [team_ids]
        # TODO: proper series tracking would need game grouping by round/series

        # Build member_teams from draft_picks or auction_bids
        # Use team_id if available, fall back to nba_team_id for backward compat
        if pool["type"] == "draft":
            picks = sb.table("draft_picks").select("*").eq("pool_id", pool_id).execute().data
            member_teams = {}
            for p in picks:
                tid = p.get("team_id") or p.get("nba_team_id")
                member_teams.setdefault(p["member_id"], []).append(tid)
        else:
            bids = sb.table("auction_bids").select("*").eq(
                "pool_id", pool_id
            ).eq("is_winning_bid", True).execute().data
            member_teams = {}
            for b in bids:
                tid = b.get("team_id") or b.get("nba_team_id")
                member_teams.setdefault(b["member_id"], []).append(tid)

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

    # Sort by score descending, assign ranks
    sorted_members = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    for rank, (member_id, total) in enumerate(sorted_members, 1):
        sb.table("pool_standings").upsert({
            "pool_id": pool_id,
            "member_id": member_id,
            "rank": rank,
            "total_points": total,
            "points_breakdown": {},
        }, on_conflict="pool_id,member_id").execute()

        # Update denormalized total on pool_members
        sb.table("pool_members").update({"total_points": total}).eq(
            "id", member_id
        ).execute()
