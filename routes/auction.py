from flask import Blueprint, render_template, request, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client

auction_bp = Blueprint("auction", __name__)


@auction_bp.route("/pool/<pool_id>/auction/bid", methods=["POST"])
@login_required
def place_bid(pool_id):
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
    team_id = data.get("team_id") or data.get("nba_team_id")
    league = data.get("league", "nba")
    bid_amount = float(data["bid_amount"])

    auction_config = pool.get("auction_config", {})
    auction_style = auction_config.get("auction_style", "budget")

    # Budget check (only for budget-style auctions)
    if auction_style == "budget":
        starting_budget = auction_config.get("starting_budget", 100)
        winning_bids = sb.table("auction_bids").select("bid_amount").eq(
            "pool_id", pool_id
        ).eq("member_id", member["id"]).eq("is_winning_bid", True).execute().data
        spent = sum(b["bid_amount"] for b in winning_bids)

        if spent + bid_amount > starting_budget:
            return jsonify({"error": f"Over budget. Spent: ${spent}, Budget: ${starting_budget}"}), 400

    # Check bid is higher than current high bid for this team+league
    current_bids = sb.table("auction_bids").select("bid_amount").eq(
        "pool_id", pool_id
    ).eq("nba_team_id", team_id).eq("league", league).order("bid_amount", desc=True).execute().data

    if current_bids and bid_amount <= current_bids[0]["bid_amount"]:
        return jsonify({"error": f"Bid must be higher than current high: ${current_bids[0]['bid_amount']}"}), 400

    sb.table("auction_bids").insert({
        "pool_id": pool_id,
        "member_id": member["id"],
        "nba_team_id": team_id,  # backward compat
        "team_id": team_id,
        "league": league,
        "bid_amount": bid_amount,
        "is_winning_bid": False,
    }).execute()

    return jsonify({"success": True})


@auction_bp.route("/pool/<pool_id>/auction/resolve", methods=["POST"])
@login_required
def resolve_team(pool_id):
    """Mark the highest bid for a team as the winning bid."""
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    pool = pool[0]

    if pool["creator_id"] != session["user_id"]:
        return jsonify({"error": "Only the creator can resolve bids"}), 403

    data = request.get_json()
    team_id = data.get("team_id") or data.get("nba_team_id")
    league = data.get("league", "nba")

    bids = sb.table("auction_bids").select("*").eq(
        "pool_id", pool_id
    ).eq("nba_team_id", team_id).eq("league", league).order("bid_amount", desc=True).execute().data

    if not bids:
        return jsonify({"error": "No bids for this team"}), 400

    winning_bid = bids[0]
    sb.table("auction_bids").update({"is_winning_bid": True}).eq(
        "id", winning_bid["id"]
    ).execute()

    return jsonify({"success": True, "winner_member_id": winning_bid["member_id"], "amount": winning_bid["bid_amount"]})
