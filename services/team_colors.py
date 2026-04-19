"""Team primary/alternate colors (hex, no #). Seeded from ESPN's teams
endpoint. Safe to hand-edit — this is just a lookup table."""

TEAM_COLORS = {
    "nba": {
        1:  {"color": "c8102e", "alt_color": "fdb927"},  # Atlanta Hawks
        2:  {"color": "008348", "alt_color": "ffffff"},  # Boston Celtics
        5:  {"color": "860038", "alt_color": "bc945c"},  # Cleveland Cavaliers
        7:  {"color": "0e2240", "alt_color": "fec524"},  # Denver Nuggets
        8:  {"color": "1d428a", "alt_color": "c8102e"},  # Detroit Pistons
        10: {"color": "ce1141", "alt_color": "000000"},  # Houston Rockets
        13: {"color": "552583", "alt_color": "fdb927"},  # Los Angeles Lakers
        16: {"color": "266092", "alt_color": "79bc43"},  # Minnesota Timberwolves
        18: {"color": "1d428a", "alt_color": "f58426"},  # New York Knicks
        19: {"color": "0150b5", "alt_color": "9ca0a3"},  # Orlando Magic
        20: {"color": "1d428a", "alt_color": "e01234"},  # Philadelphia 76ers
        21: {"color": "29127a", "alt_color": "e56020"},  # Phoenix Suns
        22: {"color": "e03a3e", "alt_color": "000000"},  # Portland Trail Blazers
        24: {"color": "000000", "alt_color": "c4ced4"},  # San Antonio Spurs
        25: {"color": "007ac1", "alt_color": "ef3b24"},  # Oklahoma City Thunder
        28: {"color": "d91244", "alt_color": "000000"},  # Toronto Raptors
    },
    "nhl": {
        1:      {"color": "231f20", "alt_color": "fdb71a"},  # Boston Bruins
        2:      {"color": "00468b", "alt_color": "fdb71a"},  # Buffalo Sabres
        6:      {"color": "00205b", "alt_color": "ff4c00"},  # Edmonton Oilers
        7:      {"color": "e30426", "alt_color": "000000"},  # Carolina Hurricanes
        8:      {"color": "121212", "alt_color": "a2aaad"},  # Los Angeles Kings
        9:      {"color": "20864c", "alt_color": "000000"},  # Dallas Stars
        10:     {"color": "c41230", "alt_color": "013a81"},  # Montreal Canadiens
        14:     {"color": "dd1a32", "alt_color": "b79257"},  # Ottawa Senators
        15:     {"color": "fe5823", "alt_color": "000000"},  # Philadelphia Flyers
        16:     {"color": "000000", "alt_color": "fdb71a"},  # Pittsburgh Penguins
        17:     {"color": "860038", "alt_color": "005ea3"},  # Colorado Avalanche
        20:     {"color": "003e7e", "alt_color": "ffffff"},  # Tampa Bay Lightning
        25:     {"color": "fc4c02", "alt_color": "000000"},  # Anaheim Ducks
        30:     {"color": "124734", "alt_color": "ae122a"},  # Minnesota Wild
        37:     {"color": "344043", "alt_color": "b4975a"},  # Vegas Golden Knights
        129764: {"color": "000000", "alt_color": "7ab2e1"},  # Utah Mammoth
    },
}

_DEFAULT = {"color": "4b5563", "alt_color": "9ca3af"}


def team_color(league, team_id):
    """Primary hex color (no #) for a (league, team_id). Returns a muted
    gray fallback if the team isn't in the table."""
    return TEAM_COLORS.get(league, {}).get(team_id, _DEFAULT)["color"]
