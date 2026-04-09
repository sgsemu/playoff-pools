from flask import Blueprint, render_template, request, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client

roster_bp = Blueprint("roster", __name__)

VALID_POSITIONS = ["PG", "SG", "SF", "PF", "C"]


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

    roster = []
    for r in raw_roster:
        player = sb.table("nba_players").select("name, position, salary_value").eq("id", r["nba_player_id"]).execute().data
        r["nba_players"] = player[0] if player else {"name": "Unknown", "position": "", "salary_value": 0}
        roster.append(r)

    players = sb.table("nba_players").select("*").order("salary_value", desc=True).execute().data

    salary_cap = pool["scoring_config"].get("salary_cap", 50000)
    spent = sum(r["salary"] for r in roster)
    remaining = salary_cap - spent
    filled_positions = [r["position"] for r in roster]

    return render_template("pool/roster.html",
        pool=pool, roster=roster, players=players,
        salary_cap=salary_cap, spent=spent, remaining=remaining,
        filled_positions=filled_positions)


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
    position = data["position"]

    if position not in VALID_POSITIONS:
        return jsonify({"error": f"Invalid position: {position}"}), 400

    # Check position not already filled
    roster = sb.table("salary_rosters").select("*").eq(
        "pool_id", pool_id
    ).eq("member_id", member["id"]).execute().data

    filled = [r["position"] for r in roster]
    if position in filled:
        return jsonify({"error": f"Position {position} already filled"}), 400

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
            "error": f"Over salary cap. Spent: ${spent}, Player: ${player['salary_value']}, Cap: ${salary_cap}"
        }), 400

    sb.table("salary_rosters").insert({
        "pool_id": pool_id,
        "member_id": member["id"],
        "nba_player_id": nba_player_id,
        "salary": player["salary_value"],
        "position": position,
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
