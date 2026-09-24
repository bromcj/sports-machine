"""The placebo's side must be random and reproducible - bets.paper.placebo_side."""
from bets.paper import placebo_side


def test_the_side_is_stable_for_a_given_game():
    # Seeded from the game_id, so it cannot be reshuffled after the result is
    # known.
    assert placebo_side("mlb-823894") == placebo_side("mlb-823894")


def test_the_sides_are_balanced():
    sides = [placebo_side(f"mlb-{i}") for i in range(800_000, 802_000)]
    home = sides.count("home")
    assert 0.45 < home / len(sides) < 0.55, home


def test_the_recorded_placebo_bets_match_it():
    # Bets 26 and 28 in the live database: game -> side the placebo took.
    assert placebo_side("mlb-824060") == "home"
    assert placebo_side("mlb-823894") == "away"
