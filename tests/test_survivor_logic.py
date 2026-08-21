from datetime import datetime, date
from zoneinfo import ZoneInfo
from services.survivor import pick_lock_at

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
