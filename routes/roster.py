from flask import Blueprint, render_template, request, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client

roster_bp = Blueprint("roster", __name__)

MAX_ROSTER_SIZE = 5


@roster_bp.route("/pool/<pool_id>/roster")
@login_required
def roster_page(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404
    pool = pool[0]

    member = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).eq("user_id", session["user_id"]).execute().data
    if not member:
        return "Not a member", 403
    member = member[0]

    raw_roster = sb.table("salary_rosters").select("*").eq(
        "pool_id", pool_id
    ).eq("member_id", member["id"]).execute().data

    # Look up player + team info for each roster entry
    roster = []
    for r in raw_roster:
        player = sb.table("nba_players").select("name, team_id, salary_value").eq("id", r["nba_player_id"]).execute().data
        if player:
            team = sb.table("nba_teams").select("name, abbreviation").eq("id", player[0]["team_id"]).execute().data
            r["player_name"] = player[0]["name"]
            r["team_name"] = team[0]["abbreviation"] if team else "?"
            r["salary_value"] = player[0]["salary_value"]
        else:
            r["player_name"] = "Unknown"
            r["team_name"] = "?"
            r["salary_value"] = 0
        roster.append(r)

    # Get all players with their team info for the picker
    all_players = sb.table("nba_players").select("*").order("salary_value", desc=True).execute().data
    teams_map = {}
    for t in sb.table("nba_teams").select("id, name, abbreviation").execute().data:
        teams_map[t["id"]] = t

    for p in all_players:
        team = teams_map.get(p["team_id"], {})
        p["team_name"] = team.get("name", "")
        p["team_abbr"] = team.get("abbreviation", "")

    salary_cap = pool["scoring_config"].get("salary_cap", 50000)
    spent = sum(r["salary"] for r in raw_roster)
    remaining = salary_cap - spent
    roster_full = len(roster) >= MAX_ROSTER_SIZE

    return render_template("pool/roster.html",
        pool=pool, roster=roster, players=all_players,
        salary_cap=salary_cap, spent=spent, remaining=remaining,
        roster_full=roster_full, roster_count=len(roster),
        max_roster=MAX_ROSTER_SIZE)


@roster_bp.route("/pool/<pool_id>/roster/pick", methods=["POST"])
@login_required
def pick_player(pool_id):
    sb = get_service_client()

    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    pool = pool[0]

    member = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).eq("user_id", session["user_id"]).execute().data
    if not member:
        return jsonify({"error": "Not a member"}), 403
    member = member[0]

    data = request.get_json()
    nba_player_id = data["nba_player_id"]

    # Check roster not already full
    roster = sb.table("salary_rosters").select("*").eq(
        "pool_id", pool_id
    ).eq("member_id", member["id"]).execute().data

    if len(roster) >= MAX_ROSTER_SIZE:
        return jsonify({"error": f"Roster full ({MAX_ROSTER_SIZE} players max)"}), 400

    # Check player not already on roster
    if any(r["nba_player_id"] == nba_player_id for r in roster):
        return jsonify({"error": "Player already on your roster"}), 400

    # Get player salary
    player = sb.table("nba_players").select("*").eq("id", nba_player_id).execute().data
    if not player:
        return jsonify({"error": "Player not found"}), 404
    player = player[0]

    # Check salary cap
    salary_cap = pool["scoring_config"].get("salary_cap", 50000)
    spent = sum(r["salary"] for r in roster)
    if spent + player["salary_value"] > salary_cap:
        return jsonify({
            "error": f"Over salary cap. Spent: ${spent:,}, Player: ${player['salary_value']:,}, Cap: ${salary_cap:,}"
        }), 400

    sb.table("salary_rosters").insert({
        "pool_id": pool_id,
        "member_id": member["id"],
        "nba_player_id": nba_player_id,
        "salary": player["salary_value"],
        "position": player.get("position", ""),
    }).execute()

    return jsonify({"success": True})


@roster_bp.route("/pool/<pool_id>/roster/remove", methods=["POST"])
@login_required
def remove_player(pool_id):
    sb = get_service_client()
    data = request.get_json()
    roster_id = data["roster_id"]

    member = sb.table("pool_members").select("id").eq(
        "pool_id", pool_id
    ).eq("user_id", session["user_id"]).execute().data
    if not member:
        return jsonify({"error": "Not a member"}), 403

    sb.table("salary_rosters").delete().eq("id", roster_id).eq(
        "member_id", member[0]["id"]
    ).execute()

    return jsonify({"success": True})
