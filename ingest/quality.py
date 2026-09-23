"""Reject malformed rows at the boundary, before they reach the database.

audit.py checks DERIVED integrity beautifully - it re-derives a rolling
feature from raw Statcast rather than trusting the code. Nothing checked
INGESTED integrity. A malformed upstream response landed in the database and
flowed into features unchallenged, and by the time it showed up it looked
like a modelling problem.

The rules here are deliberately narrow: each one rejects something that
cannot be true, not something that looks unusual. A validator that guesses
throws away real data on quiet days, which is worse than the bad row it was
meant to catch. Everything rejected is counted and printed - a rejection is a
data-quality event worth seeing, not something to swallow.

American moneylines are the subtle one. They are never 0, and never strictly
between -100 and +100: a price is either "risk this to win 100" (negative) or
"risk 100 to win this" (positive), and the region in between does not exist.
A 0 or a -50 in that column is a parsing failure upstream, not a long shot.
"""

# Beyond this a price is not a long shot, it is a mistake. The Odds API tops
# out around -100000 on a lock; anything past that is a bad parse.
ML_LIMIT = 100_000

# No sport this project touches has produced a score anywhere near this. It is
# a sanity ceiling for a parse error, not a real-world bound.
SCORE_LIMIT = 200


def moneyline(value) -> str | None:
    """Reason this is not a valid American moneyline, or None if it is."""
    if value is None or value == "":
        return None                       # absent is allowed; nonsense is not
    try:
        v = int(value)
    except (TypeError, ValueError):
        return f"moneyline {value!r} is not a number"
    if v == 0:
        return "moneyline 0 is not a price"
    if -100 < v < 100:
        return f"moneyline {v} is impossible (nothing sits between -100 and +100)"
    if abs(v) > ML_LIMIT:
        return f"moneyline {v} is beyond any real price"
    return None


def score(value) -> str | None:
    if value is None or value == "":
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return f"score {value!r} is not a number"
    if v < 0:
        return f"score {v} is negative"
    if v > SCORE_LIMIT:
        return f"score {v} is beyond anything plausible"
    return None


def teams(away, home) -> str | None:
    if not away or not str(away).strip():
        return "away team is empty"
    if not home or not str(home).strip():
        return "home team is empty"
    if str(away).strip() == str(home).strip():
        return f"a team cannot play itself ({away})"
    return None


def snapshot(away_ml, home_ml, away=None, home=None) -> str | None:
    """Reason to reject one odds row, or None to keep it."""
    if away is not None or home is not None:
        bad = teams(away, home)
        if bad:
            return bad
    for v in (away_ml, home_ml):
        bad = moneyline(v)
        if bad:
            return bad
    # A row with neither price carries no information. It is not corrupt, but
    # storing it inflates every snapshot count and tells you nothing.
    if (away_ml is None or away_ml == "") and (home_ml is None or home_ml == ""):
        return "no price on either side"
    return None


def game(away, home, game_date, away_score=None, home_score=None,
         status=None, sport="mlb") -> str | None:
    """Reason to reject one game row, or None to keep it."""
    bad = teams(away, home)
    if bad:
        return bad
    if not game_date or len(str(game_date)) < 10:
        return f"game_date {game_date!r} is not a date"
    for v in (away_score, home_score):
        bad = score(v)
        if bad:
            return bad
    # A completed game has a score. Both feeds report a POSTPONEMENT as
    # finished - ESPN with no score, which the old code turned into 0-0, and
    # the MLB Stats API with abstractGameState literally "Final" - so these
    # two rules are what stop a postponement being stored as a result that
    # cannot have happened.
    if status == "final":
        if away_score is None or home_score is None:
            return "a final with no score is not a completed game"
        if sport == "mlb" and int(away_score) == 0 and int(home_score) == 0:
            return "0-0 cannot be an MLB final (extra innings decide)"
    return None


class Rejects:
    """Collects rejections so a run can report them instead of hiding them."""

    def __init__(self, label=""):
        self.label = label
        self.reasons = []

    def check(self, reason) -> bool:
        """True to keep the row. Pass the result of a validator above."""
        if reason is None:
            return True
        self.reasons.append(reason)
        return False

    def __len__(self):
        return len(self.reasons)

    def report(self):
        if not self.reasons:
            return
        tag = f"[{self.label}] " if self.label else ""
        print(f"  {tag}rejected {len(self.reasons)} malformed row(s):")
        seen = {}
        for r in self.reasons:
            seen[r] = seen.get(r, 0) + 1
        for reason, n in sorted(seen.items(), key=lambda kv: -kv[1])[:5]:
            print(f"    {n:4d} x {reason}")
        if len(seen) > 5:
            print(f"    ... and {len(seen) - 5} other reason(s)")
