import random
from flask import Blueprint, render_template, request, jsonify, session
from routes.auth import login_required
from services.supabase_client import get_service_client
from services.competitions import (
    get_pool_competition_ids, get_draftable_teams, get_team, teams_by_ref,
)
from services.team_colors import team_logo_url

draft_bp = Blueprint("draft", __name__)


def _competition_meta(sb, competition_ids):
    """Return {competition_id: {id, league, name}} for the given ids."""
    if not competition_ids:
        return {}
    rows = sb.table("competitions").select("id,league,name").in_(
        "id", list(competition_ids)
    ).execute().data
    return {r["id"]: r for r in rows}


def _build_team_groups(sb, pool_id, taken_refs):
    """Group a pool's draftable teams by competition, splitting taken vs available.

    Returns a list of {competition, teams, available} dicts, ordered by the
    competition name, so a single-competition pool yields one group and a
    combined NBA+NHL pool yields two.
    """
    teams = get_draftable_teams(sb, pool_id)
    meta = _competition_meta(sb, {t["competition_id"] for t in teams})
    groups = {}
    for t in teams:
        cid = t["competition_id"]
        league = meta.get(cid, {}).get("league", "")
        t = dict(t)
        t["logo_url"] = team_logo_url(league, t.get("ext_id"))
        g = groups.setdefault(cid, {"competition": meta.get(cid, {"id": cid, "league": "", "name": ""}),
                                    "teams": [], "available": []})
        g["teams"].append(t)
        if t["id"] not in taken_refs:
            g["available"].append(t)
    return sorted(groups.values(), key=lambda g: g["competition"].get("name", ""))


def _get_snake_order(member_ids, num_rounds):
    """Generate snake draft order: 1-2-3...3-2-1...1-2-3...

    Each member gets `num_rounds` picks (rounds × members slots). When the
    team count isn't a multiple of the member count, the leftover teams are
    intentionally left for the commissioner to assign via /draft/assign so
    every member draws the same number of snake picks."""
    order = []
    for rnd in range(1, num_rounds + 1):
        if rnd % 2 == 1:
            order.extend([(m, rnd) for m in member_ids])
        else:
            order.extend([(m, rnd) for m in reversed(member_ids)])
    return order


_META_PALETTE = ["#ef4444", "#f59e0b", "#10b981", "#3b82f6",
                 "#8b5cf6", "#ec4899", "#14b8a6", "#f97316"]


def _name_color(name):
    """Deterministic color from display name for the meta-bar initial circles."""
    h = 0
    for ch in (name or "?"):
        h = (h * 31 + ord(ch)) & 0xffffffff
    return _META_PALETTE[h % len(_META_PALETTE)]


def _initials(name):
    """1-2 letter initials from a display name: first + last word's first letter,
    or the first two letters if only one word."""
    if not name or name == "?":
        return "?"
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return parts[0][:2].upper()


def _build_meta_bar(members, picks, snake, current_pick_index, viewer_user_id, upcoming_count=8):
    """Compute the draft-room meta-bar context: the next N pickers (with initial
    + color), the viewing user's distance to their next turn, the current
    picker's name/initial, and the most recent completed pick.

    Returns a dict the template renders. Pure function over its inputs."""
    by_id = {m["id"]: m for m in members}

    def _name(member_id):
        m = by_id.get(member_id) or {}
        return (m.get("users") or {}).get("display_name") or "?"

    upcoming = []
    for i in range(current_pick_index, min(current_pick_index + upcoming_count, len(snake))):
        mid, rnd = snake[i]
        name = _name(mid)
        upcoming.append({
            "pick_order": i + 1,
            "round": rnd,
            "member_id": mid,
            "display_name": name,
            "initial": _initials(name),
            "color": _name_color(name),
            "is_current": i == current_pick_index,
        })

    viewer_member_id = next(
        (m["id"] for m in members if m.get("user_id") == viewer_user_id), None
    )
    picks_until_turn = None
    if viewer_member_id is not None and current_pick_index < len(snake):
        for i in range(current_pick_index, len(snake)):
            if snake[i][0] == viewer_member_id:
                picks_until_turn = i - current_pick_index
                break

    current_display_name = None
    current_initial = "?"
    current_color = _META_PALETTE[0]
    if current_pick_index < len(snake):
        cur_id = snake[current_pick_index][0]
        current_display_name = _name(cur_id)
        current_initial = _initials(current_display_name)
        current_color = _name_color(current_display_name)

    last_pick = None
    if picks:
        lp = picks[-1]
        last_pick = {
            "team_name": lp.get("team_name") or "?",
            "display_name": _name(lp.get("member_id")),
            "pick_order": lp.get("pick_order"),
            "logo_url": lp.get("team_logo_url"),
        }

    is_my_turn = picks_until_turn == 0

    return {
        "upcoming": upcoming,
        "picks_until_turn": picks_until_turn,
        "is_my_turn": is_my_turn,
        "current_display_name": current_display_name,
        "current_initial": current_initial,
        "current_color": current_color,
        "last_pick": last_pick,
    }


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

    # Annotate picks with team name/abbr/logo via team_ref.
    pick_refs = [p["team_ref"] for p in picks if p.get("team_ref")]
    team_lookup = teams_by_ref(sb, pick_refs)
    pick_comp_meta = _competition_meta(
        sb, {t["competition_id"] for t in team_lookup.values()}
    )
    taken_refs = set(pick_refs)
    for p in picks:
        team = team_lookup.get(p.get("team_ref"))
        p["team_name"] = team["name"] if team else "?"
        p["team_abbr"] = team["abbreviation"] if team else "?"
        if team:
            league = pick_comp_meta.get(team["competition_id"], {}).get("league", "")
            p["team_logo_url"] = team_logo_url(league, team.get("ext_id"))
        else:
            p["team_logo_url"] = None

    team_groups = _build_team_groups(sb, pool_id, taken_refs)

    # Whose turn (snake order over the ordered member list). Floor math keeps
    # every member's pick count equal; any leftover teams are assigned
    # manually by the commissioner after the snake finishes.
    member_ids = [m["id"] for m in members]
    total_teams = sum(len(g["teams"]) for g in team_groups)
    num_rounds = max(1, total_teams // len(members)) if members else 1
    snake = _get_snake_order(member_ids, num_rounds)
    leftover_count = sum(len(g["available"]) for g in team_groups)
    current_pick_index = len(picks)
    current_turn = snake[current_pick_index] if current_pick_index < len(snake) else None

    meta = _build_meta_bar(members, picks, snake, current_pick_index, session.get("user_id"))

    template = "pool/draft_room.html" if pool["type"] == "draft" else "pool/auction_room.html"
    return render_template(template,
        pool=pool, members=members, picks=picks,
        team_groups=team_groups,
        current_turn=current_turn,
        current_pick_index=current_pick_index,
        leftover_count=leftover_count,
        meta=meta)


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

    data = request.get_json(silent=True) or {}
    team_ref = data.get("team_ref")
    if not team_ref:
        return jsonify({"error": "team_ref is required"}), 400

    comp_ids = set(get_pool_competition_ids(sb, pool_id))
    team = get_team(sb, team_ref)
    if not team or team["competition_id"] not in comp_ids:
        return jsonify({"error": "Team is not in this pool's competitions"}), 400

    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order").execute().data
    if any(p.get("team_ref") == team_ref for p in picks):
        return jsonify({"error": "Team already taken"}), 400

    all_members = _order_members_for_draft(
        sb.table("pool_members").select("*").eq("pool_id", pool_id).order("joined_at").execute().data
    )
    member_ids = [m["id"] for m in all_members]
    if not member_ids:
        return jsonify({"error": "No members in pool"}), 409

    all_team_count = len(get_draftable_teams(sb, pool_id))
    num_rounds = max(1, all_team_count // len(member_ids))
    snake = _get_snake_order(member_ids, num_rounds)
    pick_order = len(picks) + 1
    if pick_order > len(snake):
        return jsonify({"error": "Draft is complete"}), 409
    expected_member_id, current_round = snake[pick_order - 1]
    if expected_member_id != member["id"]:
        return jsonify({"error": "Not your turn"}), 403

    try:
        sb.table("draft_picks").insert({
            "pool_id": pool_id,
            "member_id": member["id"],
            "team_ref": team_ref,
            "league": team.get("league", ""),
            "pick_order": pick_order,
            "round": current_round,
        }).execute()
    except Exception:
        # Unique index (pool_id, team_ref) — concurrent duplicate pick.
        return jsonify({"error": "Team already taken"}), 400

    # Build a "Nice pick! Tell <Name> they're up!" message for the picker, so
    # the in-person draft flow has an explicit hand-off instead of a silent
    # reload. The client modal uses next_message and reloads on dismiss.
    if pick_order < len(snake):
        next_member_id = snake[pick_order][0]
        user_ids = [m["user_id"] for m in all_members]
        users = sb.table("users").select("id,display_name").in_(
            "id", user_ids
        ).execute().data if user_ids else []
        name_by_user = {u["id"]: u.get("display_name") or "?" for u in users}
        next_member = next((m for m in all_members if m["id"] == next_member_id), None)
        next_name = name_by_user.get(next_member["user_id"], "?") if next_member else "?"
        if next_member_id == member["id"]:
            next_message = "You're up again — back-to-back snake pick."
        else:
            next_message = f"Tell {next_name} they're up!"
    else:
        next_message = "Snake complete — commissioner assigns the leftovers."

    return jsonify({"success": True, "pick_order": pick_order, "next_message": next_message})


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


def _require_creator_active(sb, pool_id, user_id):
    """Return (pool, error_response) — error_response is None on success."""
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return None, (jsonify({"error": "Pool not found"}), 404)
    pool = pool[0]
    if pool["creator_id"] != user_id:
        return None, (jsonify({"error": "Creator only"}), 403)
    if pool["draft_status"] != "active":
        return None, (jsonify({"error": "Draft is not active"}), 409)
    return pool, None


@draft_bp.route("/pool/<pool_id>/draft/assign", methods=["POST"])
@login_required
def assign_pick(pool_id):
    sb = get_service_client()
    pool, err = _require_creator_active(sb, pool_id, session["user_id"])
    if err:
        return err

    data = request.get_json(silent=True) or {}
    member_id = data.get("member_id")
    team_ref = data.get("team_ref")
    if not member_id or not team_ref:
        return jsonify({"error": "member_id and team_ref are required"}), 400

    member = sb.table("pool_members").select("id").eq("id", member_id).eq("pool_id", pool_id).execute().data
    if not member:
        return jsonify({"error": "Member not in pool"}), 400

    comp_ids = set(get_pool_competition_ids(sb, pool_id))
    team = get_team(sb, team_ref)
    if not team or team["competition_id"] not in comp_ids:
        return jsonify({"error": "Team is not in this pool's competitions"}), 400

    picks = sb.table("draft_picks").select("*").eq(
        "pool_id", pool_id
    ).order("pick_order").execute().data
    if any(p.get("team_ref") == team_ref for p in picks):
        return jsonify({"error": "Team already drafted"}), 400

    num_members = max(1, len(sb.table("pool_members").select("id").eq("pool_id", pool_id).execute().data))
    pick_order = (max((p["pick_order"] for p in picks), default=0)) + 1
    current_round = ((pick_order - 1) // num_members) + 1

    try:
        sb.table("draft_picks").insert({
            "pool_id": pool_id,
            "member_id": member_id,
            "team_ref": team_ref,
            "league": team.get("league", ""),
            "pick_order": pick_order,
            "round": current_round,
        }).execute()
    except Exception:
        return jsonify({"error": "Team already drafted"}), 400

    return jsonify({"success": True, "pick_order": pick_order})


@draft_bp.route("/pool/<pool_id>/draft/remove-pick", methods=["POST"])
@login_required
def remove_pick(pool_id):
    sb = get_service_client()
    _pool, err = _require_creator_active(sb, pool_id, session["user_id"])
    if err:
        return err

    data = request.get_json(silent=True) or {}
    pick_id = data.get("pick_id")
    if not pick_id:
        return jsonify({"error": "pick_id required"}), 400

    pick = sb.table("draft_picks").select("*").eq("id", pick_id).eq("pool_id", pool_id).execute().data
    if not pick:
        return jsonify({"error": "Pick not found"}), 404

    sb.table("draft_picks").delete().eq("id", pick_id).execute()
    return jsonify({"success": True})


@draft_bp.route("/pool/<pool_id>/draft/finalize", methods=["POST"])
@login_required
def finalize_draft(pool_id):
    sb = get_service_client()
    _pool, err = _require_creator_active(sb, pool_id, session["user_id"])
    if err:
        return err

    sb.table("pools").update({"draft_status": "complete"}).eq("id", pool_id).execute()

    from routes.scores import recalculate_standings
    try:
        recalculate_standings(pool_id)
    except Exception:
        pass

    return jsonify({"success": True})


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
