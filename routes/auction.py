"""Post-snake mini-auction routes.

Workflow:
- Commissioner starts the auction (sets closes_at + increment).
- Members place bids (validated against current high + increment).
- Commissioner can seed bids on behalf of others (chat bids), delete bids,
  edit closes_at, and force-settle in emergencies.
- A Vercel cron runs every 15 min, settling any pool whose closes_at has
  passed by writing the highest bidder per team as a draft_pick.
"""
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client
from services.competitions import get_team, get_draftable_teams
from services.team_colors import team_logo_url

auction_bp = Blueprint("auction", __name__)


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def _is_past(timestamp_str):
    dt = _parse_iso(timestamp_str)
    return dt is not None and datetime.now(timezone.utc) >= dt


def _auction_guard(sb, pool_id, user_id, creator_only=False):
    """Return (pool, viewer_member, err). err is a Flask response tuple or None."""
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return None, None, (jsonify({"error": "Pool not found"}), 404)
    pool = pool[0]
    if pool["draft_status"] != "auction":
        return None, None, (jsonify({"error": "Pool is not in auction phase"}), 409)
    if creator_only and pool["creator_id"] != user_id:
        return None, None, (jsonify({"error": "Creator only"}), 403)
    member = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).eq("user_id", user_id).execute().data
    if not member:
        return None, None, (jsonify({"error": "Not a member"}), 403)
    return pool, member[0], None


def _leftover_team_refs(sb, pool_id):
    """Refs of teams in this pool's competitions that haven't been picked yet."""
    teams = get_draftable_teams(sb, pool_id)
    picks = sb.table("draft_picks").select("team_ref").eq("pool_id", pool_id).execute().data
    taken = {p["team_ref"] for p in picks if p.get("team_ref")}
    return [t["id"] for t in teams if t["id"] not in taken]


@auction_bp.route("/pool/<pool_id>/auction/start", methods=["POST"])
@login_required
def start_auction(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    pool = pool[0]
    if pool["creator_id"] != session["user_id"]:
        return jsonify({"error": "Only the creator can start the auction"}), 403
    if pool["draft_status"] not in ("active", "complete"):
        return jsonify({"error": "Snake must be active or complete before starting the auction"}), 409

    data = request.get_json(silent=True) or {}
    closes_at = data.get("closes_at")
    if not _parse_iso(closes_at):
        return jsonify({"error": "closes_at must be an ISO timestamp"}), 400
    try:
        increment = int(data.get("bid_increment", 25))
    except (TypeError, ValueError):
        return jsonify({"error": "bid_increment must be an integer"}), 400
    if increment < 1:
        return jsonify({"error": "bid_increment must be >= 1"}), 400

    sb.table("pools").update({
        "draft_status": "auction",
        "auction_closes_at": closes_at,
        "auction_bid_increment": increment,
    }).eq("id", pool_id).execute()
    return jsonify({"success": True})


@auction_bp.route("/pool/<pool_id>/auction/bid", methods=["POST"])
@login_required
def place_bid(pool_id):
    sb = get_service_client()
    pool, member, err = _auction_guard(sb, pool_id, session["user_id"])
    if err:
        return err
    if _is_past(pool.get("auction_closes_at")):
        return jsonify({"error": "Auction has closed"}), 409

    data = request.get_json(silent=True) or {}
    team_ref = data.get("team_ref")
    amount = data.get("amount")
    if not team_ref or amount is None:
        return jsonify({"error": "team_ref and amount required"}), 400
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be an integer"}), 400

    increment = int(pool.get("auction_bid_increment") or 25)
    if amount <= 0 or amount % increment != 0:
        return jsonify({"error": f"Bid must be a positive multiple of ${increment}"}), 400

    if team_ref not in set(_leftover_team_refs(sb, pool_id)):
        return jsonify({"error": "That team isn't up for auction"}), 400

    current = sb.table("auction_bids").select("bid_amount").eq(
        "pool_id", pool_id
    ).eq("team_ref", team_ref).order("bid_amount", desc=True).limit(1).execute().data
    current_high = current[0]["bid_amount"] if current else 0
    if amount < current_high + increment:
        return jsonify({"error": f"Bid must be at least ${current_high + increment}"}), 400

    sb.table("auction_bids").insert({
        "pool_id": pool_id,
        "member_id": member["id"],
        "team_ref": team_ref,
        "bid_amount": amount,
    }).execute()
    return jsonify({"success": True, "amount": amount})


@auction_bp.route("/pool/<pool_id>/auction/seed", methods=["POST"])
@login_required
def seed_bid(pool_id):
    """Commissioner records a bid that came in via chat on behalf of a member."""
    sb = get_service_client()
    pool, _viewer, err = _auction_guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    team_ref = data.get("team_ref")
    member_id = data.get("member_id")
    amount = data.get("amount")
    if not team_ref or not member_id or amount is None:
        return jsonify({"error": "team_ref, member_id, amount required"}), 400
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be an integer"}), 400

    increment = int(pool.get("auction_bid_increment") or 25)
    if amount <= 0 or amount % increment != 0:
        return jsonify({"error": f"Bid must be a positive multiple of ${increment}"}), 400

    if not sb.table("pool_members").select("id").eq("id", member_id).eq("pool_id", pool_id).execute().data:
        return jsonify({"error": "Member not in pool"}), 400
    if team_ref not in set(_leftover_team_refs(sb, pool_id)):
        return jsonify({"error": "That team isn't up for auction"}), 400

    sb.table("auction_bids").insert({
        "pool_id": pool_id,
        "member_id": member_id,
        "team_ref": team_ref,
        "bid_amount": amount,
    }).execute()
    return jsonify({"success": True})


@auction_bp.route("/pool/<pool_id>/auction/delete-bid", methods=["POST"])
@login_required
def delete_bid(pool_id):
    sb = get_service_client()
    _pool, _viewer, err = _auction_guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    bid_id = data.get("bid_id")
    if not bid_id:
        return jsonify({"error": "bid_id required"}), 400
    sb.table("auction_bids").delete().eq("id", bid_id).eq("pool_id", pool_id).execute()
    return jsonify({"success": True})


@auction_bp.route("/pool/<pool_id>/auction/update-closes-at", methods=["POST"])
@login_required
def update_closes_at(pool_id):
    sb = get_service_client()
    _pool, _viewer, err = _auction_guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    closes_at = data.get("closes_at")
    if not _parse_iso(closes_at):
        return jsonify({"error": "Invalid timestamp"}), 400
    sb.table("pools").update({"auction_closes_at": closes_at}).eq("id", pool_id).execute()
    return jsonify({"success": True})


def settle_pool(sb, pool):
    """Settle an auction pool: highest bid per team wins, written as draft_picks.

    Idempotent — re-running after partial settlement skips teams that already
    have a draft_pick. Teams with zero bids are left unassigned for commissioner
    manual-assignment via the existing draft assign form.
    """
    pool_id = pool["id"]
    leftovers = _leftover_team_refs(sb, pool_id)
    if not leftovers:
        sb.table("pools").update({"draft_status": "complete"}).eq("id", pool_id).execute()
        return {"settled": [], "skipped": []}

    bids = sb.table("auction_bids").select("*").eq("pool_id", pool_id).in_(
        "team_ref", leftovers
    ).execute().data

    high_per_team = {}
    for b in bids:
        tr = b.get("team_ref")
        if tr is None:
            continue
        prev = high_per_team.get(tr)
        if prev is None or b["bid_amount"] > prev["bid_amount"]:
            high_per_team[tr] = b

    existing = sb.table("draft_picks").select("pick_order").eq(
        "pool_id", pool_id
    ).order("pick_order", desc=True).limit(1).execute().data
    next_order = (existing[0]["pick_order"] + 1) if existing else 1

    settled = []
    for team_ref, bid in high_per_team.items():
        team = get_team(sb, team_ref)
        if not team:
            continue
        try:
            sb.table("draft_picks").insert({
                "pool_id": pool_id,
                "member_id": bid["member_id"],
                "team_ref": team_ref,
                "league": team.get("league", ""),
                "pick_order": next_order,
                "round": 0,  # 0 marks auction-settled vs snake rounds 1+.
            }).execute()
            settled.append({"team_ref": team_ref, "winner": bid["member_id"], "amount": bid["bid_amount"]})
            next_order += 1
        except Exception:
            continue

    sb.table("pools").update({"draft_status": "complete"}).eq("id", pool_id).execute()

    try:
        from routes.scores import recalculate_standings
        recalculate_standings(pool_id)
    except Exception:
        pass

    skipped = [t for t in leftovers if t not in high_per_team]
    return {"settled": settled, "skipped": skipped}


@auction_bp.route("/pool/<pool_id>/auction/force-settle", methods=["POST"])
@login_required
def force_settle(pool_id):
    sb = get_service_client()
    pool, _viewer, err = _auction_guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err
    result = settle_pool(sb, pool)
    return jsonify({"success": True, **result})


def build_auction_view(sb, pool, team_groups, members):
    """Build per-card auction state for the draft room template.

    Returns a list of card dicts (one per leftover team) plus a flat
    list of all bids for the commissioner's delete-a-bid UI.
    """
    leftovers_refs = _leftover_team_refs(sb, pool["id"])
    all_teams = {t["id"]: t for g in team_groups for t in g["teams"]}

    bids = sb.table("auction_bids").select("*").eq(
        "pool_id", pool["id"]
    ).order("created_at", desc=True).execute().data

    bids_by_team = {}
    for b in bids:
        tr = b.get("team_ref")
        if tr is None:
            continue
        bids_by_team.setdefault(tr, []).append(b)
    for tr in bids_by_team:
        bids_by_team[tr].sort(key=lambda x: -x["bid_amount"])

    name_by_member = {m["id"]: (m.get("users") or {}).get("display_name") or "?" for m in members}
    increment = int(pool.get("auction_bid_increment") or 25)

    cards = []
    for ref in leftovers_refs:
        t = all_teams.get(ref)
        if not t:
            continue
        team_bids = bids_by_team.get(ref, [])
        high = team_bids[0] if team_bids else None
        cards.append({
            "team_ref": ref,
            "team_name": t["name"],
            "team_abbr": t["abbreviation"],
            "logo_url": t.get("logo_url"),
            "grouping": t.get("grouping"),
            "high_amount": high["bid_amount"] if high else 0,
            "high_bidder": name_by_member.get(high["member_id"], "?") if high else None,
            "bid_count": len(team_bids),
            "next_min": (high["bid_amount"] if high else 0) + increment,
        })

    # Flat list of all bids for the commissioner panel.
    all_bids = []
    for b in bids:
        team = all_teams.get(b.get("team_ref"))
        all_bids.append({
            "id": b["id"],
            "team_ref": b.get("team_ref"),
            "team_name": team["name"] if team else "?",
            "member_name": name_by_member.get(b["member_id"], "?"),
            "amount": b["bid_amount"],
            "created_at": b.get("created_at"),
        })

    return cards, all_bids
