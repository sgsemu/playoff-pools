"""Pure survivor logic: pick-lock, weekly resolution, mercy rule, buyback windows.
No network or DB — takes plain dicts/values, returns decisions. DB access lives
in services/survivor_data.py; ESPN/odds elsewhere."""
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from services.scoring import match_outcomes

ET = ZoneInfo("America/New_York")


def _parse_lock_time(s):
    h, m = (int(x) for x in s.split(":"))
    return time(h, m)


def pick_lock_at(kickoff_at, week_sunday, sunday_lock_et="13:00"):
    """The instant a pick freezes: the earlier of the picked team's kickoff and
    1 PM ET on that week's Sunday. kickoff_at is an ET-aware datetime;
    week_sunday is a date."""
    anchor = datetime.combine(week_sunday, _parse_lock_time(sunday_lock_et), tzinfo=ET)
    return min(kickoff_at, anchor)


def is_locked(now, kickoff_at, week_sunday, sunday_lock_et="13:00"):
    return now >= pick_lock_at(kickoff_at, week_sunday, sunday_lock_et)


def _outcome_for(team_ext_id, game):
    """'win' | 'loss' | 'tie' for the given team in a resolved game."""
    for tid, outcome in match_outcomes(game):
        if tid == team_ext_id:
            return "tie" if outcome == "draw" else outcome
    return "loss"  # team not found in game -> treat as loss (defensive)


def resolve_week(entries, picks_by_entry, games_by_espn_id, week, mercy_after_week=7):
    """Grade one week. Only active entries are considered. Tie counts as a win
    (survive). A missing pick is a loss. If, after grading, no active entry
    survived AND week >= mercy_after_week, nobody is eliminated (mercy rule).
    Idempotent: depends only on inputs."""
    graded = {}
    survivors = 0
    for e in entries:
        if e["status"] != "active":
            continue
        pick = picks_by_entry.get(e["id"])
        if not pick:
            graded[e["id"]] = {"result": "no_pick", "survived": False}
            continue
        game = games_by_espn_id.get(pick["espn_game_id"])
        if game is None:
            # game not final yet -> leave pending, do not change status
            graded[e["id"]] = {"result": "pending", "survived": None}
            continue
        outcome = _outcome_for(pick["team_ext_id"], game)
        survived = outcome in ("win", "tie")
        graded[e["id"]] = {"result": outcome, "survived": survived}
        if survived:
            survivors += 1

    decided = [g for g in graded.values() if g["survived"] is not None]
    mercy = week >= mercy_after_week and survivors == 0 and len(decided) > 0

    out = {}
    for eid, g in graded.items():
        if g["survived"] is None:
            out[eid] = {"result": "pending", "status": "active", "eliminated_week": None}
        elif g["survived"] or mercy:
            out[eid] = {"result": g["result"], "status": "active", "eliminated_week": None}
        else:
            out[eid] = {"result": g["result"], "status": "eliminated", "eliminated_week": week}
    return out
