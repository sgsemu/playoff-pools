from flask import Blueprint, render_template, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client
from services.scoring import calculate_team_scores, calculate_salary_cap_scores

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
    teams = {t["id"]: t for t in sb.table("nba_teams").select("*").execute().data}

    # Annotate games with team names
    for g in games:
        g["home_name"] = teams.get(g["home_team_id"], {}).get("name", "?")
        g["away_name"] = teams.get(g["away_team_id"], {}).get("name", "?")

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

    return render_template("pool/scores.html",
        pool=pool, games=games, standings=standings, member_map=member_map)


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
        if pool["type"] == "draft":
            picks = sb.table("draft_picks").select("*").eq("pool_id", pool_id).execute().data
            member_teams = {}
            for p in picks:
                member_teams.setdefault(p["member_id"], []).append(p["nba_team_id"])
        else:
            bids = sb.table("auction_bids").select("*").eq(
                "pool_id", pool_id
            ).eq("is_winning_bid", True).execute().data
            member_teams = {}
            for b in bids:
                member_teams.setdefault(b["member_id"], []).append(b["nba_team_id"])

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
            "points_breakdown": {},  # Could be enriched with per-team/player detail
        }).execute()

        # Update denormalized total on pool_members
        sb.table("pool_members").update({"total_points": total}).eq(
            "id", member_id
        ).execute()
