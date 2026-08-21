"""Supabase data layer for survivor pools: entries, picks, buybacks, and the
batched board read. Pure DB access -- no network/ESPN calls, no pick-lock or
resolution logic (that lives in services/survivor.py). Takes an explicit `sb`
client so every function is trivial to unit-test with a fake double."""
try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover - postgrest always ships with supabase-py
    APIError = None

UNIQUE_VIOLATION = "23505"


class TeamAlreadyUsed(Exception):
    """Raised when an entry tries to pick a team it has already used this
    season -- either caught up front (existing picks already contain the
    team) or as a race, when the DB's unique index rejects the write."""


def _is_unique_violation(exc):
    if APIError is not None and isinstance(exc, APIError):
        return exc.code == UNIQUE_VIOLATION
    # Defensive fallback if the postgrest error type ever changes shape.
    return "duplicate key" in str(exc).lower()


def get_or_create_entry(sb, pool_id, member_id):
    """Return this member's survivor_entries row for the pool, creating one
    (status='active') if it doesn't exist yet."""
    rows = sb.table("survivor_entries").select("*").eq(
        "pool_id", pool_id
    ).eq("member_id", member_id).execute().data
    if rows:
        return rows[0]
    inserted = sb.table("survivor_entries").insert({
        "pool_id": pool_id,
        "member_id": member_id,
        "status": "active",
    }).execute().data
    return inserted[0]


def submit_pick(sb, entry, week, team_ref, espn_game_id, set_by="member", override_note=None):
    """Set (or change) an entry's pick for `week`. Raises TeamAlreadyUsed if
    `team_ref` is already on one of this entry's OTHER weeks -- checked up
    front, and again as a race guard around the write itself (the DB's
    UNIQUE(entry_id, team_ref) index is the final word). An unchanged
    resubmit of the same team_ref for the same week is not a reuse -- the
    upsert re-writes that exact (entry_id, week) row, which the unique index
    compares against OTHER rows, not itself -- so it's excluded up front and
    allowed to proceed."""
    entry_id = entry["id"]
    existing = sb.table("survivor_picks").select("team_ref, week").eq(
        "entry_id", entry_id
    ).execute().data
    used_refs = {r["team_ref"] for r in existing if r["week"] != week}
    if team_ref in used_refs:
        raise TeamAlreadyUsed(f"entry {entry_id} already used team {team_ref}")

    row = {
        "entry_id": entry_id,
        "week": week,
        "team_ref": team_ref,
        "espn_game_id": espn_game_id,
        "set_by": set_by,
        "override_note": override_note,
    }
    try:
        result = sb.table("survivor_picks").upsert(
            row, on_conflict="entry_id,week"
        ).execute()
    except Exception as exc:
        if _is_unique_violation(exc):
            raise TeamAlreadyUsed(
                f"entry {entry_id} already used team {team_ref}"
            ) from exc
        raise
    return result.data[0]


def record_buyback(sb, entry, week, kind, fee=None):
    """Log a buyback and bring the entry back to 'active'."""
    inserted = sb.table("survivor_buybacks").insert({
        "entry_id": entry["id"],
        "week": week,
        "kind": kind,
        "fee": fee,
    }).execute().data
    sb.table("survivor_entries").update({"status": "active"}).eq(
        "id", entry["id"]
    ).execute()
    return inserted[0]


def board_data(sb, pool_id):
    """Everything the status board needs, in exactly three reads: entries
    (joined to member display names), all picks for those entries, and all
    buybacks for those entries -- assembled in memory so rendering the board
    never issues a per-entry query."""
    entries = sb.table("survivor_entries").select(
        "*, pool_members(user_id, users(display_name))"
    ).eq("pool_id", pool_id).execute().data

    entry_ids = [e["id"] for e in entries]
    if not entry_ids:
        return {"entries": [], "picks": {}, "weeks": []}

    all_picks = sb.table("survivor_picks").select("*").in_(
        "entry_id", entry_ids
    ).execute().data
    all_buybacks = sb.table("survivor_buybacks").select("*").in_(
        "entry_id", entry_ids
    ).execute().data

    buybacks_by_entry = {}
    for b in all_buybacks:
        buybacks_by_entry.setdefault(b["entry_id"], []).append(b)

    picks = {}
    weeks = set()
    for p in all_picks:
        picks.setdefault(p["entry_id"], {})[p["week"]] = p
        weeks.add(p["week"])

    out_entries = []
    for e in entries:
        pool_member = e.get("pool_members") or {}
        user = pool_member.get("users") or {}
        out_entries.append({
            "id": e["id"],
            "pool_id": e["pool_id"],
            "member_id": e["member_id"],
            "status": e["status"],
            "eliminated_week": e.get("eliminated_week"),
            "display_name": user.get("display_name"),
            "buybacks": buybacks_by_entry.get(e["id"], []),
        })

    return {"entries": out_entries, "picks": picks, "weeks": sorted(weeks)}


def apply_resolution(sb, resolution, week):
    """Write a resolve_week() output for `week`: each entry's pick gets its
    graded result, and the entry's status/eliminated_week are updated."""
    for entry_id, outcome in resolution.items():
        sb.table("survivor_picks").update({"result": outcome["result"]}).eq(
            "entry_id", entry_id
        ).eq("week", week).execute()
        sb.table("survivor_entries").update({
            "status": outcome["status"],
            "eliminated_week": outcome["eliminated_week"],
        }).eq("id", entry_id).execute()
