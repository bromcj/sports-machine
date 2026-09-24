"""record_paper's decisions, isolated from the database."""
import random
import pytest
import model.validation as v


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point validation.json at a temp file so tests never touch the real one."""
    monkeypatch.setattr(v, "PATH", tmp_path / "validation.json")
    return v


STRONG = [1.4 + (i % 7 - 3) * 0.4 for i in range(60)]
MIXED = [v.SLOTS[i % 3] for i in range(60)]


def test_a_clear_result_with_good_coverage_passes(isolated):
    r = isolated.record_paper("t", STRONG, slots=MIXED, coverage=0.95)
    assert r["passed"], r["reason"]


def test_a_positive_but_noisy_average_does_not(isolated):
    noisy = [(2.97 if i % 2 else -2.85) for i in range(60)]   # mean +0.06%
    r = isolated.record_paper("t", noisy, slots=MIXED, coverage=0.95)
    assert not r["passed"]


def test_the_bet_floor_bites_on_its_own(isolated):
    r = isolated.record_paper("t", STRONG[:40], slots=MIXED[:40], coverage=0.95)
    assert not r["passed"] and "need" in r["reason"]


def test_low_coverage_blocks_and_names_itself(isolated):
    r = isolated.record_paper("t", STRONG, slots=MIXED, coverage=0.11)
    assert not r["passed"] and "COVERAGE" in r["reason"]


def test_unknown_coverage_fails_closed(isolated):
    r = isolated.record_paper("t", STRONG, slots=MIXED)
    assert not r["passed"]


def test_a_graded_set_dominated_by_one_slot_is_refused(isolated):
    lop = ["late"] * 48 + ["day"] * 6 + ["evening"] * 6
    r = isolated.record_paper("t", STRONG, slots=lop, coverage=0.80)
    assert not r["passed"]


def test_very_high_coverage_waives_the_slot_check(isolated):
    # At that point the mix IS the schedule; MLB really does play most games
    # in the evening.
    lop = ["late"] * 48 + ["day"] * 6 + ["evening"] * 6
    r = isolated.record_paper("t", STRONG, slots=lop, coverage=0.95)
    assert r["passed"], r["reason"]


def test_a_placebo_that_also_passes_blocks_the_gate(isolated):
    r = isolated.record_paper("t", STRONG, slots=MIXED, coverage=0.95,
                              placebo=STRONG)
    assert not r["passed"] and "PLACEBO" in r["reason"].upper()


def test_a_placebo_scoring_noise_does_not_block(isolated):
    rng = random.Random(3)
    noise = [rng.gauss(0, 2.9) for _ in range(60)]
    r = isolated.record_paper("t", STRONG, slots=MIXED, coverage=0.95,
                              placebo=noise)
    assert r["passed"], r["reason"]


def test_info_is_reported_per_slot(isolated):
    r = isolated.record_paper("t", STRONG, slots=MIXED, coverage=0.95)
    assert set(r["info_by_slot"]) == set(v.SLOTS)
