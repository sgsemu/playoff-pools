"""One-time-or-idempotent backfill of `teams.grouping` for the 2026 FIFA
World Cup competition.

Pulls the group structure from ESPN's standings endpoint and writes the
group letter (A..L) into each team row. Safe to re-run — just overwrites
with the current ESPN data, so if ESPN later edits a group assignment we
re-sync.

Run: python -m scripts.backfill_wc_groups
"""
import requests
from services.supabase_client import get_service_client


ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings"


def fetch_group_map():
    """Return {ext_id: group_letter} from ESPN's published standings."""
    resp = requests.get(ESPN_STANDINGS, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    mapping = {}
    for child in data.get("children", []):
        name = child.get("name") or ""
        # name is like "Group A"; the last whitespace-delimited token is the letter
        letter = name.split()[-1] if name.startswith("Group ") else name
        for entry in child.get("standings", {}).get("entries", []):
            ext_id = entry.get("team", {}).get("id")
            if ext_id is not None:
                mapping[int(ext_id)] = letter
    return mapping


def main():
    sb = get_service_client()
    comp = sb.table("competitions").select("id").eq(
        "league", "world_cup"
    ).eq("season", 2026).execute().data
    if not comp:
        raise SystemExit("No world_cup/2026 competition row in DB.")
    comp_id = comp[0]["id"]

    mapping = fetch_group_map()
    if not mapping:
        raise SystemExit("ESPN returned no group entries.")
    print(f"ESPN provided {len(mapping)} (ext_id -> group) entries.")

    updated = 0
    for ext_id, letter in mapping.items():
        sb.table("teams").update({"grouping": letter}).eq(
            "competition_id", comp_id
        ).eq("ext_id", ext_id).execute()
        updated += 1
    print(f"Wrote grouping on {updated} team rows.")


if __name__ == "__main__":
    main()
