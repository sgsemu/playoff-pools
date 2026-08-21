"""Pure survivor logic: pick-lock, weekly resolution, mercy rule, buyback windows.
No network or DB — takes plain dicts/values, returns decisions. DB access lives
in services/survivor_data.py; ESPN/odds elsewhere."""
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

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
