"""Survivor pool routes: status board + pick submission + buyback.

Pick-lock and buyback-window decisions are pure functions in
services/survivor.py; entry/pick/buyback persistence lives in
services/survivor_data.py. This module is the thin HTTP layer: auth guard,
JSON parsing, and translating decisions into status codes.
"""
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, session
from routes.auth import login_required
from services.supabase_client import get_service_client
from services.competitions import get_pool_competition_ids, get_team
from services.survivor import ET, is_locked, buyback_option
from services.survivor_data import (
    get_or_create_entry,
    submit_pick,
    record_buyback,
    board_data,
    resolve_week_for_pool,
    TeamAlreadyUsed,
)

survivor_bp = Blueprint("survivor", __name__)


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def _guard(sb, pool_id, user_id, creator_only=False):
    """Return (pool, viewer_member, err). err is a Flask response tuple or None.

    creator_only mirrors the auction blueprint's commissioner guard: the
    creator check happens before the membership lookup, so a non-member
    creator (shouldn't happen, but defensive) still gets a clean 403 rather
    than a "not a member" error that would be confusing on a commissioner
    action."""
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return None, None, (jsonify({"error": "Pool not found"}), 404)
    pool = pool[0]
    if creator_only and pool["creator_id"] != user_id:
        return None, None, (jsonify({"error": "Creator only"}), 403)
    member = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).eq("user_id", user_id).execute().data
    if not member:
        return None, None, (jsonify({"error": "Not a member"}), 403)
    return pool, member[0], None


def _validate_pick_target(sb, pool_id, week, team_ref, espn_game_id):
    """Validate that team_ref is one of the two teams in the espn_game_id
    game for `week`, scoped to the pool's competitions. Shared by the member
    pick route and the commissioner assign-pick route so both enforce the
    same "is this even a legal pick" rule -- only the lock check differs
    between them. Returns (game, err); err is a Flask response tuple or None."""
    comp_ids = get_pool_competition_ids(sb, pool_id)
    if not comp_ids:
        return None, (jsonify({"error": "Game not found"}), 400)
    games = sb.table("game_results").select(
        "kickoff_at, competition_id, home_team_id, away_team_id"
    ).eq("espn_game_id", espn_game_id).eq("week", week).in_(
        "competition_id", comp_ids
    ).execute().data
    if not games:
        return None, (jsonify({"error": "Game not found"}), 400)
    game = games[0]

    team = get_team(sb, team_ref)
    if not team or team.get("ext_id") not in (
        game.get("home_team_id"), game.get("away_team_id")
    ):
        return None, (jsonify({"error": "Team is not in that game"}), 400)

    return game, None


def _week_sunday(sb, competition_id, week):
    """The Sunday date (ET) among a week's kickoffs; falls back to the date of
    any game in that week if none happen to land on a Sunday (bye weeks,
    all-Thursday/Monday slates, etc.)."""
    if not competition_id or week is None:
        return None
    rows = sb.table("game_results").select("kickoff_at").eq(
        "competition_id", competition_id
    ).eq("week", week).execute().data
    dates = []
    for r in rows:
        dt = _parse_iso(r.get("kickoff_at"))
        if dt:
            dates.append(dt.astimezone(ET).date())
    for d in dates:
        if d.weekday() == 6:  # Sunday
            return d
    return dates[0] if dates else None


@survivor_bp.route("/pool/<pool_id>/survivor")
@login_required
def survivor_board(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404
    pool = pool[0]

    data = board_data(sb, pool_id)
    # Best-effort default: the week after the last one anyone has picked yet.
    # Task 12's real board template is free to override/refine this.
    current_week = (max(data["weeks"]) + 1) if data["weeks"] else 1

    return render_template(
        "pool/survivor_board.html",
        pool=pool, pool_id=pool_id, board=data, current_week=current_week,
    )


@survivor_bp.route("/pool/<pool_id>/survivor/pick", methods=["POST"])
@login_required
def submit_survivor_pick(pool_id):
    sb = get_service_client()
    pool, member, err = _guard(sb, pool_id, session["user_id"])
    if err:
        return err

    data = request.get_json(silent=True) or {}
    team_ref = data.get("team_ref")
    espn_game_id = data.get("espn_game_id")
    week = data.get("week")
    if not team_ref or not espn_game_id or week is None:
        return jsonify({"error": "week, team_ref, espn_game_id required"}), 400
    try:
        week = int(week)
    except (TypeError, ValueError):
        return jsonify({"error": "week must be an integer"}), 400

    game, err = _validate_pick_target(sb, pool_id, week, team_ref, espn_game_id)
    if err:
        return err

    kickoff_at = _parse_iso(game.get("kickoff_at"))
    if kickoff_at is None:
        return jsonify({"error": "Game has no kickoff time yet"}), 400
    kickoff_et = kickoff_at.astimezone(ET)

    week_sunday = _week_sunday(sb, game.get("competition_id"), week) or kickoff_et.date()

    if is_locked(datetime.now(ET), kickoff_et, week_sunday):
        return jsonify({"error": "Picks are locked for this week"}), 409

    entry = get_or_create_entry(sb, pool_id, member["id"])
    try:
        pick = submit_pick(sb, entry, week, team_ref, espn_game_id)
    except TeamAlreadyUsed as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "pick": pick})


@survivor_bp.route("/pool/<pool_id>/survivor/buyback", methods=["POST"])
@login_required
def submit_survivor_buyback(pool_id):
    sb = get_service_client()
    pool, member, err = _guard(sb, pool_id, session["user_id"])
    if err:
        return err

    data = request.get_json(silent=True) or {}
    week = data.get("week")
    if week is None:
        return jsonify({"error": "week required"}), 400
    try:
        week = int(week)
    except (TypeError, ValueError):
        return jsonify({"error": "week must be an integer"}), 400

    entry = get_or_create_entry(sb, pool_id, member["id"])
    if entry["status"] != "eliminated":
        return jsonify({"error": "not eliminated"}), 400

    config = pool.get("survivor_config") or {}
    option = buyback_option(week, config)
    if not option or not option.get("kind"):
        return jsonify({"error": "No buyback window is open for that week"}), 400

    if option["kind"] == "super":
        existing_super = sb.table("survivor_buybacks").select("id").eq(
            "entry_id", entry["id"]
        ).eq("kind", "super").execute().data
        limit = option.get("limit")
        if limit is not None and len(existing_super) >= limit:
            return jsonify({"error": "Super buyback already used"}), 400

    buyback = record_buyback(sb, entry, week, option["kind"], fee=option.get("fee"))
    return jsonify({"ok": True, "buyback": buyback})


# ---------------------------------------------------------------------------
# Commissioner tools -- all creator-only. Unlike the member-facing routes
# above, these operate on any member's entry (by member_id) and generally
# bypass the guardrails members are held to (lock time, buyback windows):
# the commissioner is the manual-override valve when reality (a text from
# someone who forgot to pick, a scoring dispute) diverges from what the app
# recorded.
# ---------------------------------------------------------------------------

def _member_in_pool(sb, pool_id, member_id):
    return bool(sb.table("pool_members").select("id").eq(
        "id", member_id
    ).eq("pool_id", pool_id).execute().data)


@survivor_bp.route("/pool/<pool_id>/survivor/assign-pick", methods=["POST"])
@login_required
def assign_pick(pool_id):
    """Commissioner sets (or replaces) a member's pick for a week, bypassing
    the lock entirely. Still enforced: the team must actually be playing in
    that game that week, and TeamAlreadyUsed still applies -- discretion over
    *when* a pick can be made, not over the one-team-once rule."""
    sb = get_service_client()
    _pool, _viewer, err = _guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    member_id = data.get("member_id")
    team_ref = data.get("team_ref")
    espn_game_id = data.get("espn_game_id")
    week = data.get("week")
    note = data.get("note")
    if not member_id or not team_ref or not espn_game_id or week is None:
        return jsonify({"error": "member_id, week, team_ref, espn_game_id required"}), 400
    try:
        week = int(week)
    except (TypeError, ValueError):
        return jsonify({"error": "week must be an integer"}), 400

    if not _member_in_pool(sb, pool_id, member_id):
        return jsonify({"error": "Member not in pool"}), 400

    _game, err = _validate_pick_target(sb, pool_id, week, team_ref, espn_game_id)
    if err:
        return err

    entry = get_or_create_entry(sb, pool_id, member_id)
    try:
        pick = submit_pick(
            sb, entry, week, team_ref, espn_game_id,
            set_by="commissioner", override_note=note,
        )
    except TeamAlreadyUsed as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "pick": pick})


@survivor_bp.route("/pool/<pool_id>/survivor/buyback-for", methods=["POST"])
@login_required
def record_buyback_for(pool_id):
    """Commissioner records a buyback on a member's behalf (e.g. cash handed
    over in person), bypassing the normal window/limit checks a self-serve
    buyback goes through."""
    sb = get_service_client()
    _pool, _viewer, err = _guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    member_id = data.get("member_id")
    week = data.get("week")
    kind = data.get("kind", "regular")
    fee = data.get("fee")
    if not member_id or week is None:
        return jsonify({"error": "member_id and week required"}), 400
    try:
        week = int(week)
    except (TypeError, ValueError):
        return jsonify({"error": "week must be an integer"}), 400
    if kind not in ("regular", "super"):
        return jsonify({"error": "kind must be 'regular' or 'super'"}), 400

    if not _member_in_pool(sb, pool_id, member_id):
        return jsonify({"error": "Member not in pool"}), 400

    entry = get_or_create_entry(sb, pool_id, member_id)
    buyback = record_buyback(sb, entry, week, kind, fee=fee)
    return jsonify({"ok": True, "buyback": buyback})


@survivor_bp.route("/pool/<pool_id>/survivor/set-status", methods=["POST"])
@login_required
def set_status(pool_id):
    """Commissioner eliminates or reinstates a member's entry directly."""
    sb = get_service_client()
    _pool, _viewer, err = _guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    member_id = data.get("member_id")
    status = data.get("status")
    eliminated_week = data.get("eliminated_week")
    if not member_id or status not in ("active", "eliminated"):
        return jsonify({"error": "member_id and status ('active'|'eliminated') required"}), 400

    if status == "active":
        eliminated_week = None
    elif eliminated_week is not None:
        try:
            eliminated_week = int(eliminated_week)
        except (TypeError, ValueError):
            return jsonify({"error": "eliminated_week must be an integer"}), 400

    if not _member_in_pool(sb, pool_id, member_id):
        return jsonify({"error": "Member not in pool"}), 400

    entry = get_or_create_entry(sb, pool_id, member_id)
    updated = sb.table("survivor_entries").update({
        "status": status,
        "eliminated_week": eliminated_week,
    }).eq("id", entry["id"]).execute().data
    return jsonify({"ok": True, "entry": updated[0] if updated else entry})


@survivor_bp.route("/pool/<pool_id>/survivor/resolve", methods=["POST"])
@login_required
def resolve_now(pool_id):
    """Re-run the resolver for one week of this pool. Idempotent: it's a pure
    function of the current entries/picks/games rows, so calling it again
    after more games go final (or with nothing changed at all) just re-derives
    and re-writes the same state -- safe to use as a manual "nudge" alongside
    the automatic resolve_and_apply() the ESPN sync path (cron + throttled
    poll) already runs on every sync. Delegates to
    services.survivor_data.resolve_week_for_pool, the same routine that path
    uses."""
    sb = get_service_client()
    pool, _viewer, err = _guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    week = data.get("week")
    if week is None:
        return jsonify({"error": "week required"}), 400
    try:
        week = int(week)
    except (TypeError, ValueError):
        return jsonify({"error": "week must be an integer"}), 400

    resolution = resolve_week_for_pool(sb, pool, week)
    return jsonify({"ok": True, "resolution": resolution})


@survivor_bp.route("/pool/<pool_id>/survivor/settle", methods=["POST"])
@login_required
def settle_season(pool_id):
    """Mark the pool settled and record the winner(s) -- whichever entries are
    still 'active' when the commissioner calls it. Minimal by design: no
    payout tracking, just enough state for the UI to show "season over" and
    who won."""
    sb = get_service_client()
    pool, _viewer, err = _guard(sb, pool_id, session["user_id"], creator_only=True)
    if err:
        return err

    entries = sb.table("survivor_entries").select("*").eq("pool_id", pool_id).execute().data
    winner_entry_ids = [e["id"] for e in entries if e.get("status") == "active"]

    config = dict(pool.get("survivor_config") or {})
    config["settled"] = True
    config["winner_entry_ids"] = winner_entry_ids

    sb.table("pools").update({
        "draft_status": "complete",
        "survivor_config": config,
    }).eq("id", pool_id).execute()

    return jsonify({"ok": True, "winner_entry_ids": winner_entry_ids})
