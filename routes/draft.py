import random
from flask import Blueprint, render_template, request, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client

draft_bp = Blueprint("draft", __name__)


def _get_snake_order(member_ids, num_rounds):
    """Generate snake draft order: 1-2-3...3-2-1...1-2-3..."""
    order = []
    for rnd in range(1, num_rounds + 1):
        if rnd % 2 == 1:
            order.extend([(m, rnd) for m in member_ids])
        else:
            order.extend([(m, rnd) for m in reversed(member_ids)])
    return order


@draft_bp.route("/pool/<pool_id>/draft")
@login_required
def draft_room(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404
    pool = pool[0]

    members = sb.table("pool_members").select(
        "*, users(display_name)"
    ).eq("pool_id", pool_id).order("joined_at").execute().data

    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order").execute().data

    teams = sb.table("nba_teams").select("*").order("seed").execute().data
    taken_team_ids = [p["nba_team_id"] for p in picks]
    available_teams = [t for t in teams if t["id"] not in taken_team_ids]

    # Determine whose turn it is
    member_ids = [m["id"] for m in members]
    num_rounds = max(1, len(teams) // len(members)) if members else 1
    snake = _get_snake_order(member_ids, num_rounds)
    current_pick_index = len(picks)
    current_turn = snake[current_pick_index] if current_pick_index < len(snake) else None

    template = "pool/draft_room.html" if pool["type"] == "draft" else "pool/auction_room.html"
    return render_template(template,
        pool=pool, members=members, picks=picks,
        available_teams=available_teams, current_turn=current_turn,
        current_pick_index=current_pick_index)


@draft_bp.route("/pool/<pool_id>/draft/pick", methods=["POST"])
@login_required
def make_pick(pool_id):
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
    nba_team_id = data["nba_team_id"]

    # Check team not already taken
    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order").execute().data

    taken_ids = [p["nba_team_id"] for p in picks]
    if nba_team_id in taken_ids:
        return jsonify({"error": "Team already taken"}), 400

    pick_order = len(picks) + 1
    # Determine round from snake order
    all_members = sb.table("pool_members").select("id").eq(
        "pool_id", pool_id
    ).order("joined_at").execute().data
    num_members = len(all_members)
    current_round = ((pick_order - 1) // num_members) + 1 if num_members > 0 else 1

    sb.table("draft_picks").insert({
        "pool_id": pool_id,
        "member_id": member["id"],
        "nba_team_id": nba_team_id,
        "pick_order": pick_order,
        "round": current_round,
    }).execute()

    return jsonify({"success": True, "pick_order": pick_order})


@draft_bp.route("/pool/<pool_id>/draft/start", methods=["POST"])
@login_required
def start_draft(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    pool = pool[0]

    if pool["creator_id"] != session["user_id"]:
        return jsonify({"error": "Only the creator can start the draft"}), 403

    sb.table("pools").update({"draft_status": "active"}).eq("id", pool_id).execute()
    return jsonify({"success": True})
