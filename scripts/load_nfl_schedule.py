# scripts/load_nfl_schedule.py
"""Load the full NFL 2026 regular-season schedule into game_results as
SCHEDULED rows (is_complete=false, 0-0) so survivor pools can show/select the
weekly slate before games are played. Uses ESPN's core API host
(sports.core.api.espn.com), which stays reachable when site.api is IP-blocked.

Idempotent: upserts on espn_game_id, so re-runs refresh rather than duplicate.
Only inserts games that are NOT yet complete — the regular sync updates actual
results/winners as games play. Requires migration 013 (game_results.is_complete)
applied first.

Run: python -m scripts.load_nfl_schedule
"""
import re
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from services.supabase_client import get_service_client

_ET = ZoneInfo("America/New_York")
_HDRS = {"User-Agent": "Mozilla/5.0"}
_CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
_TEAM_ID_RE = re.compile(r"/teams/(\d+)")


def _https(ref):
    return ref.replace("http://", "https://")


def _team_id(competitor):
    ref = (competitor.get("team") or {}).get("$ref", "")
    m = _TEAM_ID_RE.search(ref)
    return int(m.group(1)) if m else None


def _game_date(kickoff_at):
    try:
        return datetime.fromisoformat(kickoff_at.replace("Z", "+00:00")).astimezone(_ET).date().isoformat()
    except Exception:
        return None


def fetch_week_events(week):
    url = f"{_CORE}/seasons/2026/types/2/weeks/{week}/events?limit=100"
    r = requests.get(url, headers=_HDRS, timeout=20)
    r.raise_for_status()
    return [_https(i["$ref"]) for i in r.json().get("items", []) if i.get("$ref")]


def parse_event(ev_ref):
    """Return a scheduled-game dict, or None to skip (already complete / malformed)."""
    ev = requests.get(ev_ref, headers=_HDRS, timeout=20).json()
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    hid, aid = _team_id(home), _team_id(away)
    if hid is None or aid is None:
        return None
    # completion status (separate ref)
    completed = False
    status_ref = (comp.get("status") or {}).get("$ref")
    if status_ref:
        try:
            st = requests.get(_https(status_ref), headers=_HDRS, timeout=20).json()
            completed = bool((st.get("type") or {}).get("completed"))
        except Exception:
            completed = False
    if completed:
        return None  # let the regular sync record real results/winners
    return {
        "espn_game_id": str(ev["id"]),
        "home_team_id": hid,
        "away_team_id": aid,
        "kickoff_at": ev.get("date"),
        "game_date": _game_date(ev.get("date")),
    }


def main():
    sb = get_service_client()
    comp = sb.table("competitions").select("id").eq("league", "nfl").eq("season", 2026).execute().data
    if not comp:
        print("NFL 2026 competition not found — run scripts.seed_nfl first.", file=sys.stderr)
        sys.exit(1)
    comp_id = comp[0]["id"]

    total = 0
    for week in range(1, 19):
        try:
            refs = fetch_week_events(week)
        except Exception as e:
            print(f"week {week}: fetch failed ({e})", file=sys.stderr)
            continue
        loaded = 0
        for ref in refs:
            try:
                g = parse_event(ref)
            except Exception as e:
                print(f"  event {ref[-30:]}: {e}", file=sys.stderr)
                continue
            if not g:
                continue
            sb.table("game_results").upsert({
                "competition_id": comp_id,
                "espn_game_id": g["espn_game_id"],
                "home_team_id": g["home_team_id"],
                "away_team_id": g["away_team_id"],
                "home_score": 0,
                "away_score": 0,
                "winner_team_id": None,
                "is_draw": False,
                "is_complete": False,
                "stage": None,
                "league": "nfl",
                "round": 1,
                "week": week,
                "kickoff_at": g["kickoff_at"],
                "game_date": g["game_date"],
            }, on_conflict="espn_game_id").execute()
            loaded += 1
        total += loaded
        print(f"week {week}: loaded {loaded} scheduled games")
    print(f"Done — {total} scheduled NFL games upserted.")


if __name__ == "__main__":
    main()
