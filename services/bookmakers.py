"""Sportsbook display config and referral URL resolution.

Drives which bookmakers we ask The Odds API for and how they render. Each
bookmaker carries its brand color, the domain we pull the favicon from, and
the env var(s) that hold the user's personal referral URL. Multiple env var
names are supported so we can fall back across typos / renames without
breaking the chip.
"""
import os


_BOOKS = [
    {
        "key": "draftkings",
        "name": "DraftKings",
        "color": "#53D337",
        "domain": "draftkings.com",
        "referral_env": ("DRAFTKINGS_REFERRAL_URL",),
    },
    {
        "key": "caesars",
        "name": "Caesars",
        "color": "#A0843C",
        "domain": "caesars.com",
        "referral_env": ("CAESARS_REFERRAL_URL", "CEASARS_REFERRAL_URL"),
    },
    {
        "key": "betmgm",
        "name": "BetMGM",
        "color": "#B8860B",
        "domain": "betmgm.com",
        "referral_env": ("BETMGM_REFERRAL_URL",),
    },
    {
        "key": "fanduel",
        "name": "FanDuel",
        "color": "#1493FF",
        "domain": "fanduel.com",
        "referral_env": ("FANDUEL_REFERRAL_URL",),
    },
]


def _resolve_referral(env_names):
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def bookmakers():
    """Return display config for every configured bookmaker, with referral URL
    + favicon URL resolved."""
    out = []
    for b in _BOOKS:
        out.append({
            "key": b["key"],
            "name": b["name"],
            "color": b["color"],
            "domain": b["domain"],
            "favicon": f"https://www.google.com/s2/favicons?domain={b['domain']}&sz=32",
            "referral_url": _resolve_referral(b["referral_env"]),
        })
    return out


def bookmakers_by_key():
    return {b["key"]: b for b in bookmakers()}


def bookmaker_keys_param():
    """Comma-joined keys for The Odds API `bookmakers=` parameter."""
    return ",".join(b["key"] for b in _BOOKS)
