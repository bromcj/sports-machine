"""The placebo's side assignment must be random and reproducible."""
import hashlib


def placebo_side(game_id: str) -> str:
    """The same rule bets/paper.py uses."""
    return "home" if int(hashlib.sha256(game_id.encode()).hexdigest(), 16) & 1 \
        else "away"


def test_the_side_is_stable_for_a_given_game():
    # Seeded from the game_id, so it cannot be reshuffled after the result is
    # known.
    assert placebo_side("mlb-823894") == placebo_side("mlb-823894")


def test_the_sides_are_balanced():
    sides = [placebo_side(f"mlb-{i}") for i in range(800_000, 802_000)]
    home = sides.count("home")
    assert 0.45 < home / len(sides) < 0.55, home
