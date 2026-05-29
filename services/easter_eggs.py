"""Soccer-flavored slot for the pool home — kickoff countdown before the
tournament, matchday counter during, rotating football quote always."""
import datetime
import hashlib


KICKOFF_DATE = datetime.date(2026, 6, 11)


FOOTBALL_QUOTES = [
    {"text": "Football is the most important of the less important things in life.",
     "author": "Carlo Ancelotti"},
    {"text": "Some people think football is a matter of life and death. I assure you, "
             "it's much more serious than that.", "author": "Bill Shankly"},
    {"text": "I learned all about life with a ball at my feet.", "author": "Ronaldinho"},
    {"text": "Every disadvantage has its advantage.", "author": "Johan Cruyff"},
    {"text": "I am not a perfectionist, but I like to feel that things are done well.",
     "author": "Lionel Messi"},
    {"text": "Pelé is the only footballer who surpassed the boundaries of logic.",
     "author": "Johan Cruyff"},
    {"text": "We lost because we didn't win.", "author": "Ronaldo"},
    {"text": "Football is simple, but the hardest thing is to play simple football.",
     "author": "Johan Cruyff"},
]


def _today():
    return datetime.date.today()


def _quote_for_today():
    """Deterministic-per-day quote so the slot doesn't flicker across requests."""
    seed = int(hashlib.md5(_today().isoformat().encode()).hexdigest(), 16)
    return FOOTBALL_QUOTES[seed % len(FOOTBALL_QUOTES)]


def wc_slot(sb, competition):
    """Return the WC easter-egg payload, or None if `competition` isn't WC.

    Pre-kickoff: {countdown:{days,hours}, matchday:None, quote:{...}}.
    During tournament: {countdown:None, matchday:int, quote:{...}}.
    """
    if (competition or {}).get("league") != "world_cup":
        return None

    today = _today()
    quote = _quote_for_today()

    if today < KICKOFF_DATE:
        delta = KICKOFF_DATE - today
        return {
            "countdown": {"days": delta.days, "hours": 0},
            "matchday": None,
            "quote": quote,
        }

    # During tournament: matchday = 1 + count of distinct completed game-dates.
    rows = sb.table("game_results").select("game_date").eq(
        "competition_id", competition.get("id")
    ).execute().data
    dates = {r["game_date"] for r in rows if r.get("game_date")}
    matchday = len(dates) + 1
    return {"countdown": None, "matchday": matchday, "quote": quote}
