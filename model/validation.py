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

# Graded paper bets must span at least this many distinct first-pitch hours.
#
# Which games get a gradeable closing line is decided by cron timing, not at
# random. Measured on this archive: of 51 MLB games with any pregame price, the
# 7 that had one inside the window ALL started at 01:00 UTC - west-coast night
# games - while the games that missed had a median start of 22:00 UTC. One pull
# happened to land near one start-time bucket, so that bucket is the entire
# sample.
#
# Fifty bets drawn from a single bucket do not validate a model, they validate
# it on late west-coast games. The spread requirement is a blunt instrument
# and deliberately so: it cannot make the sample random, but it stops the most
# obvious way for gate 2 to look satisfied while measuring one narrow slice.
MIN_START_HOUR_SPREAD = 3

# How many standard errors a result must clear before it counts as evidence
# rather than noise.
#
# The two gates use DIFFERENT values, and the reason is the difference between
# testing once and testing every night.
#
# Gate 1 is recomputed only when someone deliberately reruns training, on the
# same fixed set of seasons. Rerunning it does not generate new evidence and
# does not give the result new chances to pass, so the conventional ~95%
# two-sigma bar is the right one.
#
# Gate 2 is re-tested EVERY NIGHT as paper bets accumulate, and each night is
# a fresh opportunity to cross the bar by luck. That is optional stopping, and
# it is not a small effect. Simulated on a zero-skill model with this
# project's own CLV spread, 3 bets a day:
#
#     testing              30d     60d    120d    180d    365d
#     once at the end     1.8%    2.5%    2.1%    2.8%    2.2%
#     every night         5.5%   10.4%   13.2%   15.3%   16.7%
#
# Two sigma checked nightly is a 15% false-pass rate, not 2.5% - six times
# looser than it looks. Raising the bar restores it, at a cost measured
# rather than guessed (180-day season, nightly):
#
#     sigma   false pass   detects a real +0.5% edge
#     2.0        13.6%             99.1%
#     2.5         5.2%             95%ish
#     3.0         1.6%             88.4%
#     3.5         0.3%             74.5%
#
# 3.0 buys back the guarantee for ~11 points of power, which is the right
# trade when the downside is staking money on a model with no edge.
#
# CALIBRATED TO A NIGHTLY CADENCE. If score() ever runs more often than once
# a day, or paper volume per day changes a lot, rerun that simulation - the
# number is empirical, not a constant of nature.
PAPER_CLV_SIGMA = 3.0
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


def _pooled_diff(rows: list[dict]) -> dict | None:
    """Pool the per-game log-loss margin across test seasons. None if unknown.

    Exact, from each season's (n, mean, sd) - the individual game losses do
    not need to travel. Standard pooling: the grand mean is the n-weighted
    mean, and the total sum of squares is the within-season variation plus
    the between-season variation of the means.

    Why pooled rather than per-season: a model can beat the market in every
    season by a margin that is noise in every season, which is exactly what
    MLB does here (2025: 0.64 SE, 2026: 0.61 SE). Pooling uses all the games
    at once and is the honest test of "is there anything here at all".
    """
    usable = [r for r in rows if r.get("n_games") and "ll_diff_sd" in r]
    if len(usable) != len(rows) or not usable:
        return None
    total = sum(r["n_games"] for r in usable)
    if total < 2:
        return None
    mean = sum(r["n_games"] * (r["logloss_market"] - r["logloss_model"])
               for r in usable) / total
    ss = 0.0
    for r in usable:
        n, sd = r["n_games"], r["ll_diff_sd"]
        m = r["logloss_market"] - r["logloss_model"]
        ss += (n - 1) * sd ** 2 + n * (m - mean) ** 2
    var = ss / (total - 1)
    se = (var / total) ** 0.5
    return {"n_games": total, "mean": mean, "se": se,
            "t": (mean / se) if se > 0 else 0.0}


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
        row = {"season": int(s["season"]),
               "logloss_model": round(float(s["logloss_model"]), 6),
               "logloss_market": round(float(s["logloss_market"]), 6),
               "beat_market": beat}
        # Optional, so an older caller that only has season averages still
        # works - it just cannot clear a real market, which is the safe way
        # round.
        if s.get("n_games") and s.get("ll_diff_sd") is not None:
            row["n_games"] = int(s["n_games"])
            row["ll_diff_sd"] = round(float(s["ll_diff_sd"]), 6)
        rows.append(row)

    pooled = _pooled_diff(rows)
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
    elif pooled is None:
        # Beat every season on the average, but the per-game spread was never
        # supplied, so there is no way to tell skill from luck. Refuse.
        cleared = False
        reason = (f"beat market in all {len(rows)} seasons, but no per-game "
                  f"spread was recorded, so the margin cannot be separated "
                  f"from noise - rerun the training script")
    elif pooled["t"] <= WALK_FORWARD_SIGMA:
        cleared = False
        reason = (f"beat market in all {len(rows)} seasons, but the pooled "
                  f"margin {pooled['mean']:+.5f} is only {pooled['t']:.2f} SE "
                  f"above zero (needs {WALK_FORWARD_SIGMA:g}) - inside the noise")
    else:
        cleared = True
        reason = (f"beat market in all {len(rows)} test seasons; pooled margin "
                  f"{pooled['mean']:+.5f}, {pooled['t']:.2f} SE above zero")

    data = _load()
    entry = data.setdefault(sport, {})
    new_entry = dict(entry)
    new_entry.update({"recorded_at": _now(), "baseline_kind": baseline_kind,
                      "cleared": cleared, "reason": reason, "seasons": rows,
                      "pooled": None if pooled is None else {
                          "n_games": pooled["n_games"],
                          "mean_ll_diff": round(pooled["mean"], 6),
                          "se": round(pooled["se"], 6),
                          "t_stat": round(pooled["t"], 3)},
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

def record_paper(sport: str, clvs, start_hours=None) -> dict:
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

        bets    old rule passes a       ZERO-skill model
        50          50.0%
        100         49.6%
        400         49.2%

    The old rule was not a weak test, it was no test - a coin flip at every
    sample size, because "is the average above zero" is exactly the question
    a symmetric noise distribution answers 50/50.

    PAPER_CLV_SIGMA is 3.0 rather than the conventional 2.0 because this gate
    is re-tested nightly; see the constant for the simulation. Under that
    cadence it holds a zero-skill model to ~1.6% over a season.

    The cost is honest and worth stating: a small edge needs a lot of
    evidence. Bets required to detect a real edge 80% of the time, nightly
    testing at sigma 3.0:

        true edge   bets     days at ~3/day
        +1.0%       ~121         ~40
        +0.5%       ~454        ~151
        +0.25%     ~1764        ~588

    That is months, not weeks. It is the correct trade: the cost of passing
    a model with no edge is losing money indefinitely; the cost of making a
    good model wait is waiting.

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
    # Unknown coverage fails closed, the same way gate 1 treats a missing
    # per-game spread. A caller that cannot say when its games started cannot
    # show the sample is not one narrow slice.
    spread = len(set(start_hours)) if start_hours is not None else 0
    varied = spread >= MIN_START_HOUR_SPREAD
    passed = enough and convincing and varied
    if not enough:
        reason = f"only {n_bets} graded paper bets, need {MIN_PAPER_BETS}"
    elif avg_clv <= 0:
        reason = f"avg CLV {avg_clv:+.2f}% over {n_bets} bets is not positive"
    elif not convincing:
        reason = (f"avg CLV {avg_clv:+.2f}% over {n_bets} bets is within noise "
                  f"(SE {se:.2f}%, needs to clear {PAPER_CLV_SIGMA:g} SE; t={t:.2f})")
    elif start_hours is None:
        reason = (f"avg CLV {avg_clv:+.2f}% over {n_bets} bets clears the noise, "
                  f"but no first-pitch times were supplied, so the sample cannot "
                  f"be shown to span more than one start-time bucket")
    elif not varied:
        reason = (f"avg CLV {avg_clv:+.2f}% over {n_bets} bets clears the noise, "
                  f"but every bet falls in {spread} start-time bucket(s) "
                  f"(need {MIN_START_HOUR_SPREAD}) - that validates a slice, "
                  f"not the model")
    else:
        reason = (f"avg CLV {avg_clv:+.2f}% over {n_bets} bets, "
                  f"{t:.1f} SE above zero, across {spread} start-time buckets")

    data = _load()
    entry = data.setdefault(sport, {})
    new_paper = {"passed": passed, "n_bets": int(n_bets),
                 "avg_clv": round(float(avg_clv), 3),
                 "sd_clv": round(sd, 3), "se_clv": round(se, 4),
                 "t_stat": round(t, 3) if t != float("inf") else None,
                 "start_hour_spread": spread,
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
