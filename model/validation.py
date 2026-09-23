"""Whether a sport's model has earned the right to have money on it.

The rule, from the README build order: walk-forward CV must beat the no-vig
market out of sample before a sport is bet. This module is that rule made
executable, because the NFL v1 run showed how easily it gets lost:

  that model was well calibrated, beat the home baseline decisively, and hit
  62-66% accuracy - and it lost to the closing line in all four test seasons.
  Run its picks through the 4% edge threshold and it fires on 74% of games,
  claims a +12.5% edge, and returns -9.3%.

Edge is not "my number differs from the market's." It is "my number is BETTER
than the market's." When your forecaster is worse than the price you are
betting into, disagreement is noise and the threshold just sells it back to
you as confidence.

Clearing takes THREE gates, mirroring the README build order. Beating the
market out of sample is step 2 of five; letting that alone open the tap would
skip calibration and paper trading entirely, and a model can edge past the
market on log-loss and still lose to the vig.

  gate 1  walk_forward   beat a REAL market in every test season   record()
  gate 2  paper_trading  50+ graded paper bets at positive CLV     record_paper()
  gate 3  armed          a human deliberately switched it on       arm()

The first two are measured and pass on their own. The third cannot: arm() is a
decision a person makes, and it refuses to run until gates 1 and 2 already
pass. Nothing in the automated pipeline calls it.

Two further rules:
  - Clearing requires a REAL de-vigged market. Beating a placeholder (a
    home-constant, a fixed 54%) clears nothing, however big the margin.
  - No record means not cleared. This fails closed: a fresh checkout, a cloud
    runner, a sport nobody has validated yet - all refuse.
"""
import datetime as dt
import json
from pathlib import Path

# Repo root, not data/: this is a decision record, not derived data. Tracking it
# in git makes "when was this sport cleared, and on what numbers" auditable.
PATH = Path(__file__).parent.parent / "validation.json"

REAL_MARKET = "market"          # de-vigged closing lines
PLACEHOLDER = "placeholder"     # home-constant or any other stand-in
MIN_PAPER_BETS = 50

# How many standard errors a result must clear before it counts as evidence
# rather than noise. Both measured gates use it, so tightening the project's
# idea of "convincing" is one edit.
#
# Two is the conventional ~95% bar. It is a floor, not a ceiling: these are
# the gates that decide whether real money gets staked, and the cost of
# passing a model that has no edge is much higher than the cost of making a
# good model wait for more data.
PAPER_CLV_SIGMA = 2.0
WALK_FORWARD_SIGMA = 2.0


def _stdev(xs) -> float:
    """Sample standard deviation. 0.0 for fewer than two points."""
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    return (sum((x - mean) ** 2 for x in xs) / (n - 1)) ** 0.5


def _load() -> dict:
    if not PATH.exists():
        return {}
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save(data: dict) -> None:
    PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")



def _same_except_time(a: dict, b: dict) -> bool:
    """Equal ignoring the recorded_at / armed_at stamps."""
    strip = lambda d: {k: v for k, v in d.items() if not k.endswith('_at')}
    if strip(a) != strip(b):
        return False
    for k in set(a) | set(b):
        if isinstance(a.get(k), dict) and isinstance(b.get(k), dict):
            if not _same_except_time(a[k], b[k]):
                return False
    return True

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def record(sport: str, baseline_kind: str, seasons: list[dict]) -> dict:
    """Gate 1: a walk-forward result, and whether it beat a real market.

    seasons: one dict per test season with keys
             season, logloss_model, logloss_market.
    """
    if baseline_kind not in (REAL_MARKET, PLACEHOLDER):
        raise ValueError(f"baseline_kind must be {REAL_MARKET!r} or {PLACEHOLDER!r}")

    rows = []
    for s in seasons:
        beat = float(s["logloss_model"]) < float(s["logloss_market"])
        rows.append({"season": int(s["season"]),
                     "logloss_model": round(float(s["logloss_model"]), 6),
                     "logloss_market": round(float(s["logloss_market"]), 6),
                     "beat_market": beat})

    lost = [r["season"] for r in rows if not r["beat_market"]]
    if baseline_kind == PLACEHOLDER:
        cleared = False
        reason = ("baseline is a placeholder, not a real market - beating it "
                  "proves the model learned something, not that it has edge")
    elif not rows:
        cleared, reason = False, "no test seasons"
    elif lost:
        cleared = False
        reason = (f"lost to market in {len(lost)} of {len(rows)} seasons "
                  f"({', '.join(map(str, lost))})")
    else:
        cleared = True
        reason = f"beat market in all {len(rows)} test seasons"

    data = _load()
    entry = data.setdefault(sport, {})
    new_entry = dict(entry)
    new_entry.update({"recorded_at": _now(), "baseline_kind": baseline_kind,
                      "cleared": cleared, "reason": reason, "seasons": rows,
                      # A new walk-forward supersedes any earlier decision
                      # to stake money.
                      "armed": False})
    # Rewrite only when the VERDICT changed. Re-running training with the
    # same results otherwise churns just the timestamp, dirtying the working
    # tree and making machine_daily.bat refuse to pull.
    if entry and _same_except_time(entry, new_entry):
        return entry
    data[sport] = new_entry
    _save(data)
    return new_entry

def record_paper(sport: str, clvs) -> dict:
    """Gate 2: paper-traded picks held closing-line value that is not noise.

    CLV is the honest scoreboard - it says you got a better price than the
    market settled at, which is what edge looks like before variance buries
    it. Win rate over 50 bets is mostly noise; CLV is less noisy, but it is
    NOT noise-free, and the previous version of this gate ignored that.

    It passed on `n >= 50 and avg_clv > 0`. Measured on this project's own
    archive, per-bet CLV between the first and last pregame price has a
    standard deviation of ~2.97%, so the standard error over 50 bets is
    ~0.42% and a model with no skill whatsoever clears "average is above
    zero" about half the time. That is a coin flip wearing a lab coat.

    So the bar is the average beating zero by PAPER_CLV_SIGMA standard
    errors, which needs the spread of the individual bets, not just their
    mean. `clvs` is the list of per-bet CLV percentages.

    Simulated against this archive's spread, 20k trials per cell:

        bets    old rule passes a       new rule passes a
                ZERO-skill model        ZERO-skill model
        50          50.0%                    2.7%
        100         49.6%                    2.4%
        400         49.2%                    2.3%

    The old rule was not a weak test, it was no test - a coin flip at every
    sample size, because "is the average above zero" is exactly the question
    a symmetric noise distribution answers 50/50.

    The cost is honest and worth stating: a small edge now needs a lot of
    evidence. Bets required to detect a real edge 80% of the time:

        true edge   bets
        +1.0%       ~77
        +0.5%       ~291
        +0.25%      ~1378

    At roughly three qualifying MLB bets a day that is months, not weeks.
    That is the correct trade. The cost of passing a model with no edge is
    losing money indefinitely; the cost of making a good model wait is
    waiting.

    The 50-bet floor stays as a separate, independent condition: a handful
    of lucky bets can clear a t-statistic, and n is the cheaper guard.
    """
    clvs = [float(c) for c in clvs]
    n_bets = len(clvs)
    avg_clv = sum(clvs) / n_bets if n_bets else 0.0
    sd = _stdev(clvs)
    se = sd / (n_bets ** 0.5) if n_bets else 0.0
    # se == 0 means every bet had an identical CLV. Degenerate, and almost
    # certainly synthetic, but a positive mean with no spread is unambiguous.
    t = (avg_clv / se) if se > 0 else (float("inf") if avg_clv > 0 else 0.0)
    margin = avg_clv - PAPER_CLV_SIGMA * se

    enough = n_bets >= MIN_PAPER_BETS
    convincing = margin > 0
    passed = enough and convincing
    if not enough:
        reason = f"only {n_bets} graded paper bets, need {MIN_PAPER_BETS}"
    elif avg_clv <= 0:
        reason = f"avg CLV {avg_clv:+.2f}% over {n_bets} bets is not positive"
    elif not convincing:
        reason = (f"avg CLV {avg_clv:+.2f}% over {n_bets} bets is within noise "
                  f"(SE {se:.2f}%, needs to clear {PAPER_CLV_SIGMA:g} SE; t={t:.2f})")
    else:
        reason = (f"avg CLV {avg_clv:+.2f}% over {n_bets} bets, "
                  f"{t:.1f} SE above zero")

    data = _load()
    entry = data.setdefault(sport, {})
    new_paper = {"passed": passed, "n_bets": int(n_bets),
                 "avg_clv": round(float(avg_clv), 3),
                 "sd_clv": round(sd, 3), "se_clv": round(se, 4),
                 "t_stat": round(t, 3) if t != float("inf") else None,
                 "reason": reason, "recorded_at": _now()}
    old_paper = entry.get("paper_trading") or {}
    if old_paper and _same_except_time(old_paper, new_paper):
        return old_paper
    entry["paper_trading"] = new_paper
    if not passed:
        entry["armed"] = False
    _save(data)
    return new_paper


def arm(sport: str) -> str:
    """Gate 3: a person decides this sport may stake money.

    Deliberately not called anywhere in the pipeline. Refuses unless both
    measured gates already pass, so it cannot be used to skip them.
    """
    g = gates(sport)
    if not g["walk_forward"]:
        return f"REFUSED - gate 1 not passed. {explain(sport)}"
    if not g["paper_trading"]:
        paper = (status(sport) or {}).get("paper_trading") or {}
        return ("REFUSED - gate 2 not passed: "
                + paper.get("reason", "no paper trading recorded"))
    data = _load()
    data[sport]["armed"] = True
    data[sport]["armed_at"] = _now()
    _save(data)
    return f"ARMED - {sport} may now stake money. Undo with disarm({sport!r})."


def disarm(sport: str) -> str:
    data = _load()
    if sport in data:
        data[sport]["armed"] = False
        _save(data)
    return f"{sport} disarmed."


def status(sport: str) -> dict | None:
    return _load().get(sport)


def gates(sport: str) -> dict:
    """The three gates, each True or False."""
    s = status(sport) or {}
    paper = s.get("paper_trading") or {}
    return {"walk_forward": bool(s.get("cleared")),
            "paper_trading": bool(paper.get("passed")),
            "armed": bool(s.get("armed"))}


def is_cleared(sport: str) -> bool:
    """True only when all three gates pass. A missing record means False."""
    return all(gates(sport).values())


def explain(sport: str) -> str:
    s = status(sport)
    if s is None:
        return (f"{sport}: NOT CLEARED - nothing recorded. "
                f"Run the sport's build_training script.")
    g = gates(sport)
    if all(g.values()):
        return f"{sport}: CLEARED - all three gates passed"
    paper = s.get("paper_trading") or {}
    bits = [
        "walk-forward " + ("PASS" if g["walk_forward"]
                           else "FAIL (" + str(s.get("reason", "?")) + ")"),
        "paper-trading " + ("PASS" if g["paper_trading"]
                            else "FAIL (" + paper.get("reason", "nothing recorded") + ")"),
        "armed " + ("YES" if g["armed"] else "NO (a human must call arm())"),
    ]
    return f"{sport}: NOT CLEARED - " + "; ".join(bits)


def report() -> str:
    data = _load()
    if not data:
        return "No sports validated yet. Nothing is cleared to bet."
    return "\n".join(explain(s) for s in sorted(data))


if __name__ == "__main__":
    print(report())
