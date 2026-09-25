"""Venues: every place a price comes from, and what each may be used for.

  sportsbook:<book>   prices via The Odds API (metered). Decision support
                      only: the machine says what to bet, the owner clicks.
                      Paper positions only, never automated at the book.
  kalshi              an exchange. Free market data. Paper (and, from Phase
                      C, its demo environment) only in this brief.
  polymarket          a price source. Free public data. No execution at all.

Each venue has one adapter module here that turns its native payload into
markets and prices rows (scanner.store). Adding a venue is a file plus a line
in VENUES.
"""
import config

SPORTSBOOK = "sportsbook:"

VENUES = {
    # metered: costs Odds API credits. paper: a paper order may be placed.
    "sportsbook": {"metered": True, "paper": True},
    "kalshi": {"metered": False, "paper": True},
    "polymarket": {"metered": False, "paper": False},
}


def family(venue: str) -> str:
    """'sportsbook:draftkings' -> 'sportsbook'. Raises on an unknown venue."""
    fam = str(venue).split(":", 1)[0]
    if fam not in VENUES:
        raise ValueError(f"unknown venue {venue!r}")
    if fam == "sportsbook":
        if not venue.startswith(SPORTSBOOK) or not venue[len(SPORTSBOOK):]:
            raise ValueError("a sportsbook venue needs a book: 'sportsbook:<book>'")
    elif venue != fam:
        raise ValueError(f"unknown venue {venue!r}")
    return fam


def book_venue(book: str) -> str:
    """'draftkings' -> 'sportsbook:draftkings'."""
    return f"{SPORTSBOOK}{book}"


def is_metered(venue: str) -> bool:
    return VENUES[family(venue)]["metered"]


def allowed(venue: str, sport: str | None) -> tuple[bool, str | None]:
    """May this venue be used for this market at all? (ok, reason if not).

    The ONE place the legal kill switch is read. `sport` is the market's
    sport, None for a non-game market. Every Kalshi path - adapter, poll,
    fair value, paper order - asks here, so config.KALSHI_SPORTS_ENABLED =
    False stops all of them together.
    """
    fam = family(venue)
    if fam == "kalshi" and sport and not config.KALSHI_SPORTS_ENABLED:
        return False, ("Kalshi sports markets are switched off "
                       "(config.KALSHI_SPORTS_ENABLED = False)")
    return True, None


def paper_allowed(venue: str, sport: str | None) -> tuple[bool, str | None]:
    """May a paper order be placed here?"""
    ok, why = allowed(venue, sport)
    if not ok:
        return ok, why
    if not VENUES[family(venue)]["paper"]:
        return False, f"{venue} is a price source only; nothing is executed there"
    return True, None
