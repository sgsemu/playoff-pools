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
        "pool_id, role, pools(id, name, type, league, draft_status, season_year, created_at)"
    ).eq("user_id", session["user_id"]).execute().data

    pools = [m["pools"] | {"role": m["role"]} for m in memberships if m.get("pools")]
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

    members = sb.table("pool_members").select(
        "*, users(display_name, email)"
    ).eq("pool_id", pool_id).order("total_points", desc=True).execute().data

    standings = sb.table("pool_standings").select("*").eq(
        "pool_id", pool_id
    ).order("rank").execute().data

    return render_template("pool/home.html", pool=pool, members=members, standings=standings)


@pools_bp.route("/join/<invite_code>")
@login_required
def join_pool(invite_code):
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
