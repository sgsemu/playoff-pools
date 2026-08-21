"""Survivor pool routes: status board + pick submission + buyback.

Pick-lock and buyback-window decisions are pure functions in
services/survivor.py; entry/pick/buyback persistence lives in
services/survivor_data.py. This module is the thin HTTP layer: auth guard,
JSON parsing, and translating decisions into status codes.
"""
from datetime import datetime, time
from flask import Blueprint, request, jsonify, render_template, session
from routes.auth import login_required
from services.supabase_client import get_service_client
from services.competitions import get_pool_competition_ids, get_team, teams_by_ref
from services.survivor import ET, is_locked, buyback_option
from services.survivor_data import (
    get_or_create_entry,
    submit_pick,
    record_buyback,
    board_data,
    resolve_week_for_pool,
    TeamAlreadyUsed,
)
from services.odds import get_event_for_game, best_spread_by_team
from services.team_colors import team_logo_url

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


def _week_lock_at(sb, pool_id, week):
    """The board-display lock instant for a whole week: that week's Sunday
    1 PM ET, mirroring pick_lock_at's anchor. This is a display-only
    simplification of the real per-pick lock (which can be earlier, e.g. a
    Thursday night game) -- submit_survivor_pick still enforces the precise
    per-pick lock; this just decides when the status board is allowed to
    reveal the current week's picks. Returns None if there's no game data
    yet to derive a Sunday from (e.g. week not scheduled/ingested)."""
    comp_ids = get_pool_competition_ids(sb, pool_id)
    if not comp_ids:
        return None
    week_sunday = _week_sunday(sb, comp_ids[0], week)
    if not week_sunday:
        return None
    return datetime.combine(week_sunday, time(13, 0), tzinfo=ET)


def _team_nickname(name):
    """Last word of an ESPN display name ("Pittsburgh Steelers" -> "Steelers"),
    which is how every current NFL team's nickname is formed. Falls back to
    the full name if it's somehow empty/single-word."""
    parts = (name or "").split()
    return parts[-1] if parts else (name or "")


def _resolve_current_week(sb, comp_ids):
    """The week the pick screen should show: the earliest week that still has
    at least one game not yet locked, or the last (max) week with games if
    every week is already fully locked. A game with no kickoff_at yet is
    conservatively treated as "open" (we can't prove it's locked)."""
    if not comp_ids:
        return None
    rows = sb.table("game_results").select("week, kickoff_at").in_(
        "competition_id", comp_ids
    ).execute().data
    weeks = sorted({r["week"] for r in rows if r.get("week") is not None})
    if not weeks:
        return None
    now = datetime.now(ET)
    for w in weeks:
        week_sunday = _week_sunday(sb, comp_ids[0], w)
        for r in rows:
            if r["week"] != w:
                continue
            kickoff_at = _parse_iso(r.get("kickoff_at"))
            if kickoff_at is None:
                return w
            kickoff_et = kickoff_at.astimezone(ET)
            ws = week_sunday or kickoff_et.date()
            if not is_locked(now, kickoff_et, ws):
                return w
    return weeks[-1]


def _team_spread_points(event, home_name, away_name):
    """(home_point, away_point) from an Odds API event's best spread line,
    matched by team NAME (not home/away position) since Odds API and ESPN
    don't always agree on which side is home. Either or both may be None if
    there's no event or no spreads market for a team."""
    if not event:
        return None, None
    spreads = best_spread_by_team(event)
    home_point = away_point = None
    for name, point in spreads.items():
        n = (name or "").strip().lower()
        if n == (home_name or "").strip().lower():
            home_point = point
        elif n == (away_name or "").strip().lower():
            away_point = point
    return home_point, away_point


def _pick_board_data(sb, pool_id, member):
    """Everything the pick screen (view + SWR revalidation) needs for the
    member's current week: this week's games enriched with logo/spread per
    team, which teams the member has already used (greyed, tagged with the
    week used), and the member's current pick for the week, if any."""
    comp_ids = get_pool_competition_ids(sb, pool_id)
    week = _resolve_current_week(sb, comp_ids)
    if week is None:
        return {"week": None, "week_lock_at": None, "locked": False,
                "games": [], "current_pick": None}

    games_rows = sb.table("game_results").select(
        "espn_game_id, competition_id, week, kickoff_at, home_team_id, away_team_id"
    ).eq("week", week).in_("competition_id", comp_ids).execute().data

    teams_rows = sb.table("teams").select("*").in_(
        "competition_id", comp_ids
    ).execute().data
    teams_by_ext = {t["ext_id"]: t for t in teams_rows}

    comps = sb.table("competitions").select("*").in_("id", comp_ids).execute().data
    league_by_comp = {c["id"]: c.get("league") or "nfl" for c in comps}

    entries = sb.table("survivor_entries").select("*").eq(
        "pool_id", pool_id
    ).eq("member_id", member["id"]).execute().data
    entry = entries[0] if entries else None

    existing_picks = []
    if entry:
        existing_picks = sb.table("survivor_picks").select("*").eq(
            "entry_id", entry["id"]
        ).execute().data

    # Every OTHER week's pick makes that team unusable this week; this week's
    # own pick (if any) is the current selection, not a "used" team.
    used_week_by_ref = {}
    current_pick_row = None
    for p in existing_picks:
        if p["week"] == week:
            current_pick_row = p
        else:
            used_week_by_ref[p["team_ref"]] = p["week"]

    week_sunday = _week_sunday(sb, comp_ids[0], week) if comp_ids else None
    week_lock_at = _week_lock_at(sb, pool_id, week)
    now = datetime.now(ET)
    locked = week_lock_at is not None and now >= week_lock_at

    def _team_view(team, point):
        ref = team["id"]
        league = league_by_comp.get(team.get("competition_id"), "nfl")
        return {
            "team_ref": ref,
            "name": team.get("name"),
            "nickname": _team_nickname(team.get("name")),
            "abbreviation": team.get("abbreviation"),
            "logo_url": team_logo_url(league, team.get("ext_id")),
            "spread": point,
            "used_week": used_week_by_ref.get(ref),
            "selected": bool(current_pick_row and current_pick_row.get("team_ref") == ref),
        }

    games = []
    current_pick_view = None
    for g in games_rows:
        home_team = teams_by_ext.get(g.get("home_team_id"))
        away_team = teams_by_ext.get(g.get("away_team_id"))
        if not home_team or not away_team:
            continue
        league = league_by_comp.get(g.get("competition_id"), "nfl")
        kickoff_at = _parse_iso(g.get("kickoff_at"))
        kickoff_et = kickoff_at.astimezone(ET) if kickoff_at else None
        early_lock = bool(kickoff_et and week_sunday and kickoff_et.date() != week_sunday)

        odds_event = get_event_for_game({
            "league": league,
            "home": {"name": home_team.get("name")},
            "away": {"name": away_team.get("name")},
        })
        home_point, away_point = _team_spread_points(
            odds_event, home_team.get("name"), away_team.get("name")
        )

        home_view = _team_view(home_team, home_point)
        away_view = _team_view(away_team, away_point)
        if home_view["selected"]:
            current_pick_view = home_view
        if away_view["selected"]:
            current_pick_view = away_view

        games.append({
            "espn_game_id": g.get("espn_game_id"),
            "week": week,
            "kickoff_at": kickoff_at.isoformat() if kickoff_at else None,
            "kickoff_label": kickoff_et.strftime("%a %-I:%M %p ET") if kickoff_et else "TBD",
            "early_lock": early_lock,
            "early_lock_label": kickoff_et.strftime("%a").upper() if early_lock and kickoff_et else None,
            "home": home_view,
            "away": away_view,
        })

    return {
        "week": week,
        "week_lock_at": week_lock_at.isoformat() if week_lock_at else None,
        "locked": locked,
        "games": games,
        "current_pick": current_pick_view,
    }


@survivor_bp.route("/pool/<pool_id>/survivor/pick")
@login_required
def survivor_pick_view(pool_id):
    sb = get_service_client()
    pool, member, err = _guard(sb, pool_id, session["user_id"])
    if err:
        return err

    data = _pick_board_data(sb, pool_id, member)

    return render_template(
        "pool/_survivor_pick.html",
        pool=pool, pool_id=pool_id, data=data,
    )


@survivor_bp.route("/pool/<pool_id>/survivor/pick.json")
@login_required
def survivor_pick_json(pool_id):
    """JSON twin of the pick view, for the client's stale-while-revalidate
    cache (static/survivor.js) -- same data, same _pick_board_data call, so
    the two can never drift out of sync with each other."""
    sb = get_service_client()
    _pool, member, err = _guard(sb, pool_id, session["user_id"])
    if err:
        return err

    return jsonify(_pick_board_data(sb, pool_id, member))


@survivor_bp.route("/pool/<pool_id>/survivor")
@login_required
def survivor_board(pool_id):
    sb = get_service_client()
    pool = sb.table("pools").select("*").eq("id", pool_id).execute().data
    if not pool:
        return "Pool not found", 404
    pool = pool[0]

    data = board_data(sb, pool_id)
    # Display-only default for the header and the range of week columns
    # shown: the week after the last one anyone has picked yet. This must
    # NEVER gate which picks are hidden -- weeks_with_picks grows the
    # instant anyone submits a pick, so using it for hiding would flip an
    # early week from "current/hidden" to "past/revealed" the moment one
    # member picks early, leaking that pick to every other member days
    # before the week actually locks. See week_locked below for the real
    # per-week hiding rule.
    current_week = (max(data["weeks"]) + 1) if data["weeks"] else 1

    # Team abbreviations for every team_ref referenced by any pick, resolved
    # in one batched read here (not per-cell in the template) so board_data's
    # "exactly three reads" batching intent still holds for its own tests --
    # this is a fourth read the route layer adds on top.
    team_refs = {
        pick["team_ref"]
        for entry_picks in data["picks"].values()
        for pick in entry_picks.values()
        if pick.get("team_ref")
    }
    team_abbrs = {
        ref: team.get("abbreviation", ref)
        for ref, team in teams_by_ref(sb, team_refs).items()
    }

    # Per-week lock state, computed independently for EVERY week column the
    # board shows, from that week's OWN lock instant (never from whether
    # anyone has picked it yet). Conservative default: if we can't yet
    # determine a week's lock instant (no game data ingested), treat it as
    # NOT locked so the board keeps hiding rather than accidentally
    # revealing early.
    now = datetime.now(ET)
    week_locked = {}
    for w in range(1, current_week + 1):
        lock_at = _week_lock_at(sb, pool_id, w)
        week_locked[w] = lock_at is not None and now >= lock_at
    current_week_locked = week_locked.get(current_week, False)

    # A member always sees their OWN pick, even pre-lock -- only other
    # members' picks are hidden for an unlocked week. This is a fifth read
    # (on top of board_data's three plus team_abbrs' one): find the viewing
    # user's membership in this pool, then their entry, if any.
    viewer_member = sb.table("pool_members").select("id").eq(
        "pool_id", pool_id
    ).eq("user_id", session["user_id"]).execute().data
    viewer_member_id = viewer_member[0]["id"] if viewer_member else None
    viewer_entry_id = next(
        (e["id"] for e in data["entries"] if e["member_id"] == viewer_member_id),
        None,
    ) if viewer_member_id else None

    alive_count = sum(1 for e in data["entries"] if e["status"] == "active")

    return render_template(
        "pool/survivor_board.html",
        pool=pool, pool_id=pool_id, board=data, current_week=current_week,
        team_abbrs=team_abbrs, current_week_locked=current_week_locked,
        week_locked=week_locked, viewer_entry_id=viewer_entry_id,
        alive_count=alive_count, total_count=len(data["entries"]),
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
