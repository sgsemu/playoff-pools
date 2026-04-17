import secrets
from flask import Blueprint, render_template, request, redirect, session, flash
from routes.auth import login_required
from services.supabase_client import get_service_client

pools_bp = Blueprint("pools", __name__)


@pools_bp.route("/")
def landing():
    if session.get("user_id"):
        return redirect("/dashboard")
    return render_template("landing.html")


def _build_scoring_config(form):
    scoring_type = form.get("scoring_type", "per_win")
    config = {"type": scoring_type}

    if scoring_type == "per_win":
        config["points_per_win"] = int(form.get("points_per_win", 1))
    elif scoring_type == "per_round":
        config["round_1"] = int(form.get("round_1_points", 2))
        config["round_2"] = int(form.get("round_2_points", 4))
        config["round_3"] = int(form.get("round_3_points", 6))
        config["round_4"] = int(form.get("round_4_points", 10))
    elif scoring_type == "combo":
        config["points_per_win"] = int(form.get("points_per_win", 1))
        config["round_1_bonus"] = int(form.get("round_1_points", 2))
        config["round_2_bonus"] = int(form.get("round_2_points", 4))
        config["round_3_bonus"] = int(form.get("round_3_points", 6))
        config["round_4_bonus"] = int(form.get("round_4_points", 10))
    elif scoring_type == "salary_cap":
        config["stat_points"] = float(form.get("stat_points_mult", 1))
        config["stat_rebounds"] = float(form.get("stat_rebounds_mult", 0))
        config["stat_assists"] = float(form.get("stat_assists_mult", 0))
        config["salary_cap"] = int(form.get("salary_cap", 50000))

    return config


def _build_auction_config(form):
    auction_style = form.get("auction_style", "budget")
    config = {"auction_style": auction_style}
    if auction_style == "budget":
        config["starting_budget"] = int(form.get("starting_budget", 100))
    return config


@pools_bp.route("/dashboard")
@login_required
def dashboard():
    sb = get_service_client()
    memberships = sb.table("pool_members").select(
        "pool_id, role"
    ).eq("user_id", session["user_id"]).execute().data

    pools = []
    for m in memberships:
        pool_data = sb.table("pools").select("*").eq("id", m["pool_id"]).execute().data
        if pool_data:
            pools.append(pool_data[0] | {"role": m["role"]})

    active = [p for p in pools if p["draft_status"] != "complete"]
    past = [p for p in pools if p["draft_status"] == "complete"]

    return render_template("dashboard.html", active_pools=active, past_pools=past)


@pools_bp.route("/pool/create", methods=["GET", "POST"])
@login_required
def create_pool():
    if request.method == "GET":
        return render_template("pool/create.html")

    sb = get_service_client()
    invite_code = secrets.token_urlsafe(8)
    pool_type = request.form["type"]

    scoring_config = _build_scoring_config(request.form)
    auction_config = _build_auction_config(request.form) if pool_type == "auction" else {}

    pool = sb.table("pools").insert({
        "creator_id": session["user_id"],
        "name": request.form["name"],
        "league": "nba",
        "type": pool_type,
        "invite_code": invite_code,
        "buy_in": request.form.get("buy_in", ""),
        "payout_description": request.form.get("payout_description", ""),
        "scoring_config": scoring_config,
        "auction_config": auction_config,
        "draft_mode": request.form.get("draft_mode", "live"),
        "timer_seconds": int(request.form.get("timer_seconds", 60)),
        "season_year": 2026
    }).execute().data[0]

    # Add creator as a member
    sb.table("pool_members").insert({
        "pool_id": pool["id"],
        "user_id": session["user_id"],
        "role": "creator"
    }).execute()

    flash(f"Pool created! Share this invite link: {__import__('config').APP_URL}/join/{invite_code}", "success")
    return redirect(f"/pool/{pool['id']}")


@pools_bp.route("/pool/<pool_id>")
@login_required
def pool_home(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        flash("Pool not found.", "error")
        return redirect("/dashboard")
    pool = pool[0]

    raw_members = sb.table("pool_members").select("*").eq(
        "pool_id", pool_id
    ).order("total_points", desc=True).execute().data

    members = []
    for m in raw_members:
        user_data = sb.table("users").select("display_name, email").eq("id", m["user_id"]).execute().data
        m["users"] = user_data[0] if user_data else {"display_name": "Unknown", "email": ""}
        members.append(m)

    standings = sb.table("pool_standings").select("*").eq(
        "pool_id", pool_id
    ).order("rank").execute().data

    # Build member → teams mapping for standings detail
    picks = sb.table("draft_picks").select("*").eq("pool_id", pool_id).order("pick_order").execute().data
    nba_teams = {t["id"]: t for t in sb.table("nba_teams").select("*").execute().data}
    nhl_teams = {t["id"]: t for t in sb.table("nhl_teams").select("*").execute().data}

    # Count wins from game_results (same source as scoring engine)
    all_games = sb.table("game_results").select("*").execute().data
    team_wins = {}
    for g in all_games:
        winner_id = g["home_team_id"] if g["home_score"] > g["away_score"] else g["away_team_id"]
        team_wins[winner_id] = team_wins.get(winner_id, 0) + 1

    member_teams = {}
    for p in picks:
        tid = p.get("team_id") or p.get("nba_team_id")
        league = p.get("league", "nba")
        team = nba_teams.get(tid) if league == "nba" else nhl_teams.get(tid)
        if team:
            member_teams.setdefault(p["member_id"], []).append({
                "name": team["name"],
                "abbreviation": team["abbreviation"],
                "league": league,
                "wins": team_wins.get(tid, 0),
            })

    return render_template("pool/home.html", pool=pool, members=members,
        standings=standings, member_teams=member_teams)


@pools_bp.route("/join/<invite_code>")
def join_pool(invite_code):
    if "user_id" not in session:
        session["pending_invite"] = invite_code
        return redirect("/register")

    sb = get_service_client()
    pools = sb.table("pools").select("*").eq("invite_code", invite_code).execute().data
    if not pools:
        flash("Invalid invite link.", "error")
        return redirect("/dashboard")

    pool = pools[0]

    # Check if already a member
    existing = sb.table("pool_members").select("id").eq(
        "pool_id", pool["id"]
    ).eq("user_id", session["user_id"]).execute().data

    if not existing:
        sb.table("pool_members").insert({
            "pool_id": pool["id"],
            "user_id": session["user_id"],
            "role": "member"
        }).execute()
        flash(f"You joined {pool['name']}!", "success")
    else:
        flash(f"You're already in {pool['name']}.", "error")

    return redirect(f"/pool/{pool['id']}")


@pools_bp.route("/api/pool/<pool_id>", methods=["DELETE"])
@login_required
def delete_pool(pool_id):
    from flask import jsonify
    sb = get_service_client()
    pool = sb.table("pools").select("id, creator_id").eq("id", pool_id).execute().data
    if not pool:
        return jsonify({"error": "Pool not found"}), 404
    if pool[0]["creator_id"] != session["user_id"]:
        return jsonify({"error": "Only the creator can delete a pool"}), 403

    # Delete in order: picks/bids, standings, members, then pool
    sb.table("draft_picks").delete().eq("pool_id", pool_id).execute()
    sb.table("auction_bids").delete().eq("pool_id", pool_id).execute()
    sb.table("salary_rosters").delete().eq("pool_id", pool_id).execute()
    sb.table("pool_standings").delete().eq("pool_id", pool_id).execute()
    sb.table("pool_members").delete().eq("pool_id", pool_id).execute()
    sb.table("pools").delete().eq("id", pool_id).execute()

    return jsonify({"ok": True})
