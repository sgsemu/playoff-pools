"""Playoff quotes shown under pool titles. Edit this list freely —
it's a plain Python module, no migration needed. The quote of the day is
picked deterministically by date so everyone sees the same quote at the
same time."""

import datetime

QUOTES = [
    # NBA
    ("Michael Jordan", "I've failed over and over and over again in my life. And that is why I succeed."),
    ("Kobe Bryant", "Greatness is not for everybody."),
    ("Rasheed Wallace", "Ball don't lie."),
    ("Kevin Garnett", "Anything is possible!"),
    ("Larry Bird", "Leadership is diving for a loose ball."),

    # NHL
    ("Wayne Gretzky — Michael Scott", "You miss 100% of the shots you don't take."),
    ("Mark Messier", "Leaders aren't born, they're made. And they're made just like anything else — through hard work."),
    ("Scotty Bowman", "Statistics are like a lamppost to a drunken man — more for leaning on than illumination."),

    # Coaches / general
    ("Vince Lombardi", "It's not whether you get knocked down; it's whether you get up."),
    ("Mike Tyson", "Everyone has a plan until they get punched in the mouth."),
    ("John Wooden", "You can make mistakes, but you're not a failure until you start blaming others for those mistakes."),

    # Pool-flavored
    ("Anonymous", "In the playoffs, everybody's a prophet until Game 1."),
]


def quote_of_the_day():
    """Same quote for all viewers on a given day."""
    idx = datetime.date.today().toordinal() % len(QUOTES)
    author, text = QUOTES[idx]
    return {"author": author, "text": text}
