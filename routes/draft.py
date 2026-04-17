import random
from flask import Blueprint, render_template, request, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client

draft_bp = Blueprint("draft", __name__)


def _get_all_teams(sb):
    """Fetch both NBA and NHL teams, tagged with league."""
    nba = sb.table("nba_teams").select("*").order("seed").execute().data
    for t in nba:
        t["league"] = "nba"
    nhl = sb.table("nhl_teams").select("*").order("seed").execute().data
    for t in nhl:
        t["league"] = "nhl"
    return nba + nhl


def _get_snake_order(member_ids, num_rounds):
    """Generate snake draft order: 1-2-3...3-2-1...1-2-3..."""
    order = []
    for rnd in range(1, num_rounds + 1):
        if rnd % 2 == 1:
            order.extend([(m, rnd) for m in member_ids])
        else:
            order.extend([(m, rnd) for m in reversed(member_ids)])
    return order


def _order_members_for_draft(members):
    """Sort members by draft_position (nulls last), then joined_at ascending.

    Members whose draft_position has never been set fall to the end,
    preserving join-order among themselves. New joiners therefore land
    at the end automatically.
    """
    def key(m):
        pos = m.get("draft_position")
        return (0, pos, m.get("joined_at") or "") if pos is not None else (1, 0, m.get("joined_at") or "")
    return sorted(members, key=key)


@draft_bp.route("/pool/<pool_id>/draft")
@login_required
def draft_room(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404
    pool = pool[0]

    raw_members = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).order("joined_at").execute().data
    raw_members = _order_members_for_draft(raw_members)
    members = []
    for m in raw_members:
        user = sb.table("users").select("display_name").eq("id", m["user_id"]).execute().data
        m["users"] = user[0] if user else {"display_name": "Unknown"}
        members.append(m)

    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order").execute().data

    all_teams = _get_all_teams(sb)
    # Build taken set: (league, team_id)
    taken = set()
    for p in picks:
        league = p.get("league", "nba")
        team_id = p.get("team_id") or p.get("nba_team_id")
        taken.add((league, team_id))

    nba_available = [t for t in all_teams if t["league"] == "nba" and ("nba", t["id"]) not in taken]
    nhl_available = [t for t in all_teams if t["league"] == "nhl" and ("nhl", t["id"]) not in taken]

    # Determine whose turn it is
    member_ids = [m["id"] for m in members]
    total_teams = len(all_teams)
    num_rounds = max(1, total_teams // len(members)) if members else 1
    snake = _get_snake_order(member_ids, num_rounds)
    current_pick_index = len(picks)
    current_turn = snake[current_pick_index] if current_pick_index < len(snake) else None

    template = "pool/draft_room.html" if pool["type"] == "draft" else "pool/auction_room.html"
    return render_template(template,
        pool=pool, members=members, picks=picks,
        nba_available=nba_available, nhl_available=nhl_available,
        current_turn=current_turn,
        current_pick_index=current_pick_index)


@draft_bp.route("/pool/<pool_id>/draft/pick", methods=["POST"])
@login_required
def make_pick(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    pool = pool[0]

    if pool["draft_status"] != "active":
        return jsonify({"error": "Draft is not active"}), 409

    member = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).eq("user_id", session["user_id"]).execute().data
    if not member:
        return jsonify({"error": "Not a member"}), 403
    member = member[0]

    data = request.get_json()
    team_id = data.get("team_id") or data.get("nba_team_id")
    league = data.get("league", "nba")

    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order").execute().data

    for p in picks:
        p_league = p.get("league", "nba")
        p_team_id = p.get("team_id") or p.get("nba_team_id")
        if p_league == league and p_team_id == team_id:
            return jsonify({"error": "Team already taken"}), 400

    all_members = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).order("joined_at").execute().data
    all_members = _order_members_for_draft(all_members)
    member_ids = [m["id"] for m in all_members]
    num_members = len(member_ids)
    if num_members == 0:
        return jsonify({"error": "No members in pool"}), 409

    all_teams = _get_all_teams(sb)
    num_rounds = max(1, len(all_teams) // num_members)
    snake = _get_snake_order(member_ids, num_rounds)

    pick_order = len(picks) + 1
    if pick_order > len(snake):
        return jsonify({"error": "Draft is complete"}), 409
    expected_member_id, current_round = snake[pick_order - 1]
    if expected_member_id != member["id"]:
        return jsonify({"error": "Not your turn"}), 403

    sb.table("draft_picks").insert({
        "pool_id": pool_id,
        "member_id": member["id"],
        "nba_team_id": team_id,
        "team_id": team_id,
        "league": league,
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


@draft_bp.route("/pool/<pool_id>/draft/undo", methods=["POST"])
@login_required
def undo_last_pick(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    pool = pool[0]

    if pool["creator_id"] != session["user_id"]:
        return jsonify({"error": "Only the creator can undo picks"}), 403

    if pool["draft_status"] != "active":
        return jsonify({"error": "Draft is not active"}), 409

    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order", desc=True).execute().data

    if not picks:
        return jsonify({"error": "No picks to undo"}), 409

    last = picks[0]
    sb.table("draft_picks").delete().eq("id", last["id"]).execute()
    return jsonify({"success": True, "undone_pick_order": last["pick_order"]})


@draft_bp.route("/pool/<pool_id>/draft/order", methods=["POST"])
@login_required
def set_draft_order(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    pool = pool[0]

    if pool["creator_id"] != session["user_id"]:
        return jsonify({"error": "Only the creator can set draft order"}), 403

    if pool["draft_status"] != "pending":
        return jsonify({"error": "Draft order is locked once the draft has started"}), 409

    data = request.get_json(silent=True) or {}
    submitted = data.get("member_ids")
    if not isinstance(submitted, list) or not submitted:
        return jsonify({"error": "member_ids must be a non-empty list"}), 400

    current = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).order("joined_at").execute().data
    current_ids = {m["id"] for m in current}

    if len(submitted) != len(set(submitted)):
        return jsonify({"error": "member_ids contains duplicates"}), 400
    if set(submitted) != current_ids:
        return jsonify({"error": "member_ids must match current pool members"}), 400

    for position, member_id in enumerate(submitted, start=1):
        sb.table("pool_members").update(
            {"draft_position": position}
        ).eq("id", member_id).execute()

    return jsonify({"success": True})
