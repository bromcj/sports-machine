"""validation.only_paper_changed: when scheduled_check.bat may discard a local
validation.json before pulling.

bets/paper.score() rewrites the file every time a paper bet settles, and the
scheduled job refuses to run on a dirty tree - so without this, each night's
settlement made the next morning's run refuse. Discarding must only ever fail
closed: it may throw away the recomputable gate-2 block, never a decision.
"""
import copy

from model.validation import only_paper_changed

COMMITTED = {
    "mlb": {"armed": False, "baseline_kind": "market", "cleared": False,
            "reason": "lost to market in 3 of 3 seasons",
            "seasons": [{"season": 2024, "beat_market": False}],
            "paper_trading": {"passed": False, "n_bets": 1, "coverage": 0.125,
                              "reason": "only 1 graded paper bets, need 50"}},
    "nfl": {"armed": False, "baseline_kind": "market", "cleared": False},
}


def _local():
    return copy.deepcopy(COMMITTED)


def test_a_new_paper_record_may_be_discarded():
    local = _local()
    local["mlb"]["paper_trading"].update(n_bets=2, coverage=0.2,
                                         reason="only 2 graded paper bets")
    assert only_paper_changed(COMMITTED, local)


def test_a_first_paper_record_for_a_sport_may_be_discarded():
    local = _local()
    local["nfl"]["paper_trading"] = {"passed": False, "n_bets": 3}
    local["nfl"]["armed"] = False
    assert only_paper_changed(COMMITTED, local)


def test_a_changed_gate_1_record_is_kept():
    local = _local()
    local["mlb"]["reason"] = "beat market in all 3 seasons"
    assert not only_paper_changed(COMMITTED, local)


def test_a_human_arming_is_kept():
    local = _local()
    local["mlb"]["armed"] = True
    assert not only_paper_changed(COMMITTED, local)


def test_a_disarm_by_a_failing_gate_2_is_kept():
    committed = copy.deepcopy(COMMITTED)
    committed["mlb"]["armed"] = True
    committed["mlb"]["paper_trading"]["passed"] = True
    local = copy.deepcopy(committed)
    local["mlb"]["armed"] = False
    local["mlb"]["paper_trading"]["passed"] = False
    assert not only_paper_changed(committed, local)


def test_a_stale_committed_pass_is_never_restored():
    committed = copy.deepcopy(COMMITTED)
    committed["mlb"]["paper_trading"]["passed"] = True
    assert not only_paper_changed(committed, _local())


def test_discarding_a_local_pass_is_allowed_because_it_fails_closed():
    local = _local()
    local["mlb"]["paper_trading"]["passed"] = True
    assert only_paper_changed(COMMITTED, local)


def test_an_unreadable_local_file_is_kept():
    # _load() returns {} for a file with conflict markers in it.
    assert not only_paper_changed(COMMITTED, {})
