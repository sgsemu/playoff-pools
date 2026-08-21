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


def _guard(sb, pool_id, user_id):
    """Return (pool, viewer_member, err). err is a Flask response tuple or None."""
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return None, None, (jsonify({"error": "Pool not found"}), 404)
    pool = pool[0]
    member = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).eq("user_id", user_id).execute().data
    if not member:
        return None, None, (jsonify({"error": "Not a member"}), 403)
    return pool, member[0], None


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

    comp_ids = get_pool_competition_ids(sb, pool_id)
    if not comp_ids:
        return jsonify({"error": "Game not found"}), 400
    games = sb.table("game_results").select(
        "kickoff_at, competition_id, home_team_id, away_team_id"
    ).eq("espn_game_id", espn_game_id).eq("week", week).in_(
        "competition_id", comp_ids
    ).execute().data
    if not games:
        return jsonify({"error": "Game not found"}), 400
    game = games[0]

    team = get_team(sb, team_ref)
    if not team or team.get("ext_id") not in (
        game.get("home_team_id"), game.get("away_team_id")
    ):
        return jsonify({"error": "Team is not in that game"}), 400

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
