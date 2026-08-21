from datetime import datetime, date
from zoneinfo import ZoneInfo
from services.survivor import pick_lock_at, resolve_week

ET = ZoneInfo("America/New_York")


def _et(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=ET)


def test_thursday_pick_locks_at_kickoff():
    # TNF Thu 8:15 PM, week Sunday is the 13th -> lock at Thursday kickoff
    lock = pick_lock_at(_et(2026, 9, 10, 20, 15), date(2026, 9, 13))
    assert lock == _et(2026, 9, 10, 20, 15)


def test_sunday_late_pick_locks_at_1pm():
    # 4:25 PM Sunday game -> capped at 1 PM Sunday
    lock = pick_lock_at(_et(2026, 9, 13, 16, 25), date(2026, 9, 13))
    assert lock == _et(2026, 9, 13, 13, 0)


def test_monday_night_pick_locks_at_1pm_sunday():
    lock = pick_lock_at(_et(2026, 9, 14, 20, 15), date(2026, 9, 13))
    assert lock == _et(2026, 9, 13, 13, 0)


def test_saturday_pick_locks_at_kickoff():
    lock = pick_lock_at(_et(2026, 12, 20, 13, 0), date(2026, 12, 21))
    assert lock == _et(2026, 12, 20, 13, 0)


def _game(gid, home, away, winner=None, draw=False):
    return {"espn_game_id": gid, "home_team_id": home, "away_team_id": away,
            "home_score": 1 if winner==home else 0, "away_score": 1 if winner==away else 0,
            "is_draw": draw, "winner_team_id": winner}


def test_resolver_win_survives_loss_eliminates():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None},
               {"id": "e2", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 1, "team_ext_id": 10, "espn_game_id": "g1"},
             "e2": {"week": 1, "team_ext_id": 20, "espn_game_id": "g1"}}
    games = {"g1": _game("g1", 10, 20, winner=10)}
    r = resolve_week(entries, picks, games, week=1)
    assert r["e1"]["status"] == "active" and r["e1"]["result"] == "win"
    assert r["e2"]["status"] == "eliminated" and r["e2"]["eliminated_week"] == 1


def test_resolver_tie_is_win():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 3, "team_ext_id": 10, "espn_game_id": "g1"}}
    games = {"g1": _game("g1", 10, 20, draw=True)}
    r = resolve_week(entries, picks, games, week=3)
    assert r["e1"]["result"] == "tie" and r["e1"]["status"] == "active"


def test_resolver_no_pick_eliminates():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None}]
    r = resolve_week(entries, {}, {}, week=2)
    assert r["e1"]["result"] == "no_pick" and r["e1"]["status"] == "eliminated"


def test_resolver_mercy_all_lose_after_week7_all_survive():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None},
               {"id": "e2", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 8, "team_ext_id": 10, "espn_game_id": "g1"},
             "e2": {"week": 8, "team_ext_id": 30, "espn_game_id": "g2"}}
    games = {"g1": _game("g1", 10, 20, winner=20), "g2": _game("g2", 30, 40, winner=40)}
    r = resolve_week(entries, picks, games, week=8)
    assert all(v["status"] == "active" for v in r.values())
    assert all(v["result"] == "loss" for v in r.values())  # graded loss, but not eliminated


def test_resolver_mercy_not_before_week7():
    entries = [{"id": "e1", "status": "active", "eliminated_week": None}]
    picks = {"e1": {"week": 5, "team_ext_id": 10, "espn_game_id": "g1"}}
    games = {"g1": _game("g1", 10, 20, winner=20)}
    r = resolve_week(entries, picks, games, week=5)
    assert r["e1"]["status"] == "eliminated"
