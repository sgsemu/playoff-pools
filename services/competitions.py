"""Read helpers for the competition registry.

A pool draws its draftable teams from one or more competitions via the
pool_competitions join table. Single-league pools link one competition;
combined NBA+NHL pools link two. Functions take an explicit Supabase client
so they are trivial to unit-test.
"""


def get_pool_competition_ids(sb, pool_id):
    """Return the competition ids a pool drafts from (possibly empty)."""
    rows = sb.table("pool_competitions").select("competition_id").eq(
        "pool_id", pool_id
    ).execute().data
    return [r["competition_id"] for r in rows]


def get_draftable_teams(sb, pool_id):
    """Return the team rows draftable in a pool, across all its competitions.

    Each row carries its own competition_id, so callers never need to branch
    on league. Returns [] when the pool links no competitions.
    """
    competition_ids = get_pool_competition_ids(sb, pool_id)
    if not competition_ids:
        return []
    return sb.table("teams").select("*").in_(
        "competition_id", competition_ids
    ).execute().data


def get_team(sb, team_ref):
    """Return a single team row by its id, or None."""
    rows = sb.table("teams").select("*").eq("id", team_ref).execute().data
    return rows[0] if rows else None


def teams_by_ref(sb, refs):
    """Return {team_id: team_row} for the given team ids. {} for empty input."""
    if not refs:
        return {}
    rows = sb.table("teams").select("*").in_("id", list(refs)).execute().data
    return {r["id"]: r for r in rows}
