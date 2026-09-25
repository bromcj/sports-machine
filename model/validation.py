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
market out of sample is step 2 of four; letting that alone open the tap would
skip calibration and paper trading entirely, and a model can edge past the
market on log-loss and still lose to the vig.

  gate 1  walk_forward   beat a REAL market in every test season,  record()
                         pooled t > 2, no one season carrying it
  gate 2  paper_trading  50+ graded paper bets, mean INFO 3 SE     record_paper()
                         above zero, coverage, slots, placebo
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

# Which games get a gradeable closing line is decided by cron timing, not at
# random. Measured on this archive: of 51 MLB games with any pregame price, the
# 7 with one inside the window ALL started at 01:00 UTC - west-coast night
# games - while the games that missed had a median start of 22:00 UTC.
#
# The first attempt at guarding this counted distinct UTC start HOURS and asked
# for 3. That is far too easy: the late west-coast slice alone spans 00:xx
# (Colorado at 8:40pm ET), 01:xx and 02:xx, so the very sample that motivated
# the rule passes it. So would 48 bets in one hour plus one in each of two
# others.
#
# The real question is not "how spread out are the graded bets" but "are the
# graded bets a random sample of the bets placed". That is measurable directly.
MIN_COVERAGE = 0.75          # graded / settled. Below this, gate 2 fails.

# Backstop for the case where coverage is adequate but still lopsided. Slots
# are ET: day (before 5pm), evening (5-8:59pm), late (9pm or later).
MAX_SLOT_SHARE = 0.60
# Above this coverage the slot mix IS the real schedule, so stop second-
# guessing it - MLB genuinely plays most games in the evening.
COVERAGE_WAIVES_SLOTS = 0.90

SLOTS = ("day", "evening", "late")


def slot_of(et_hour: int) -> str:
    """ET first-pitch hour -> day | evening | late."""
    if et_hour < 17:
        return "day"
    return "evening" if et_hour < 21 else "late"

# How many standard errors a result must clear before it counts as evidence
# rather than noise.
#
# Gate 1 is recomputed only on a deliberate retrain over fixed seasons, so the
# conventional two-sigma bar is right. Gate 2 is re-tested EVERY NIGHT as paper
# bets accumulate, which is optional stopping: at two sigma a zero-skill model
# passes 15.3% of the time over a season, not 2.5%. Three sigma restores the
# guarantee for about 11 points of power.
#
# The simulations behind both numbers are in docs/gates.md. They are empirical:
# if score() runs more often than nightly, or paper volume changes a lot, rerun
# the SEQUENTIAL TESTING section of audit.py before touching these. Both have
# happened (score() runs at 11:30 and 22:00; see docs/gates.md), and the
# simulation has not yet been rerun for it.
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
    season by a margin that is noise in every season, which is what MLB did
    against its old placeholder baseline (2025 at 0.75 SE, 2026 at 0.89 SE).
    Against the real closing line it now loses every season outright - see
    validation.json. Pooling uses all the games at once and is the honest test
    of "is there anything here at all". Per-season figures move whenever the
    model or the baseline changes; audit.py recomputes them rather than
    trusting this note.
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


def leave_one_season_out(rows: list[dict]) -> dict | None:
    """Pooled margin with each season dropped in turn. None if not computable.

    Pooling answers "is there anything here", but not "does it rest on one
    season". Against its old placeholder baseline, MLB's pooled margin cleared
    2 SE almost entirely on 2024: drop that season and t fell from 2.37 to
    1.16, which is nothing. (Against the real closing line every
    leave-one-out t is negative - validation.json.) A model whose whole case
    is one year out of three has not shown an edge, it has shown a year.

    Returns {"worst_season": s, "worst_t": t, "all": {season: t}}.
    """
    if len(rows) < 3:
        return None                     # dropping one leaves too little
    out = {}
    for drop in rows:
        kept = [r for r in rows if r is not drop]
        pooled = _pooled_diff(kept)
        if pooled is None:
            return None
        out[drop["season"]] = pooled["t"]
    worst = min(out, key=out.get)
    return {"worst_season": worst, "worst_t": out[worst], "all": out}


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
        loso = leave_one_season_out(rows)
        if loso is not None and loso["worst_t"] <= 0:
            cleared = False
            reason = (f"beat market in all {len(rows)} seasons and pooled at "
                      f"{pooled['t']:.2f} SE, but dropping {loso['worst_season']} "
                      f"turns the margin NEGATIVE (t={loso['worst_t']:.2f}) - the "
                      f"result rests on one season")
        elif loso is not None and loso["worst_t"] < 1.0:
            cleared = False
            reason = (f"beat market in all {len(rows)} seasons and pooled at "
                      f"{pooled['t']:.2f} SE, but without {loso['worst_season']} "
                      f"only {loso['worst_t']:.2f} SE remains - too concentrated "
                      f"in one season to call an edge")
        else:
            cleared = True
            reason = (f"beat market in all {len(rows)} test seasons; pooled "
                      f"margin {pooled['mean']:+.5f}, {pooled['t']:.2f} SE above "
                      f"zero" + ("" if loso is None else
                                 f", {loso['worst_t']:.2f} SE with "
                                 f"{loso['worst_season']} dropped"))

    data = _load()
    entry = data.setdefault(sport, {})
    new_entry = dict(entry)
    new_entry.update({"recorded_at": _now(), "baseline_kind": baseline_kind,
                      "cleared": cleared, "reason": reason, "seasons": rows,
                      "leave_one_season_out": None if not rows else (
                          leave_one_season_out(rows) or None),
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

def record_backtest(name: str, experiment: str, passed: bool, reason: str,
                    evidence: dict, metric: str | None = None) -> dict:
    """Gate 1 for a scanner STRATEGY: its own pre-registered backtest.

    A strategy is not a forecaster, so its gate 1 is not a walk-forward
    against the close: it is the one historical test its entry in
    docs/experiments.md names, with that entry's pass rule
    (scanner.gates.record_backtest checks the entry exists). The block has
    the same shape as a sport's - cleared, reason, recorded_at, armed,
    paper_trading - so gates(), arm() and only_paper_changed() treat it the
    same way. Like record(), a new result disarms. The block also holds the
    strategy's definition - its experiment and the metric its gate 2
    measures - and a result for a different one is refused: a new
    definition starts under a new name, with none of the old one's
    positions or gate-2 record.

    It never writes onto a sport's block, or onto any block that is not
    already a strategy's: a sport's gate 1 is record()'s walk-forward against
    a real market, and a typed-in backtest must not stand in for it.
    """
    import config
    if not isinstance(passed, bool):
        raise ValueError("passed must be True or False")
    if not evidence:
        raise ValueError("a backtest verdict needs its evidence")
    data = _load()
    existing = data.get(name)
    if name in config.SPORTS or (existing and existing.get("kind") != "strategy"):
        raise ValueError(f"{name!r} is a sport's block (or another non-strategy"
                         f" block): its gate 1 is a walk-forward, record(),"
                         f" never a backtest")
    old = (existing or {}).get("experiment")
    if old is not None and old.strip() != experiment.strip():
        # A different experiment is a different definition. The name is what
        # its positions, looks and gate 2 are filed under, so under this name
        # gate 2 would be re-passed on the OLD definition's positions.
        raise ValueError(f"{name}'s gate record is for {old!r}, not"
                         f" {experiment!r}: a new definition is a new strategy -"
                         f" register it under a new name")
    if existing and existing.get("metric") != metric:
        # The same, for what gate 2 measures: flipped from realized_ev (which
        # cannot pass yet) to info and re-recorded, gate 2 passed on the
        # same positions (re-verification 2026-09-25).
        raise ValueError(f"{name}'s gate record measures {existing.get('metric')!r},"
                         f" not {metric!r}: a new definition is a new strategy -"
                         f" register it under a new name")
    entry = data.setdefault(name, {})
    new_entry = dict(entry)
    new_entry.update({"kind": "strategy", "baseline_kind": "backtest",
                      "experiment": experiment, "metric": metric, "cleared": passed,
                      "reason": reason, "backtest": evidence,
                      "recorded_at": _now(), "armed": False})
    if entry and _same_except_time(entry, new_entry):
        return entry
    data[name] = new_entry
    _save(data)
    return new_entry


def record_paper(sport: str, clvs, slots=None, coverage=None,
                 placebo=None, metric="info", refuse=None) -> dict:
    """Gate 2: the model moved the fair line its way, by more than noise.

    `clvs` is the per-bet INFO component, not raw CLV. CLV against the same
    book's close has the vig in it, so a bet can beat it and still lose money,
    and it cannot separate a good price from a good forecast - place() shops
    four books, and outlier prices regress toward consensus, so shopping alone
    produces positive CLV. bets/paper.py:_decompose_bet splits every bet into
    `shop` (the price) and `info` (the fair line moving the model's way), and
    this gate tests info. Win rate over 50 bets is mostly noise; info is less
    noisy, but it is NOT noise-free, which the first version of this gate
    ignored.

    It passed on `n >= 50 and avg_clv > 0`. Measured on this project's own
    archive, per-bet CLV between the first and last pregame price has a
    standard deviation of ~2.97%, so the standard error over 50 bets is
    ~0.42% and a model with no skill whatsoever clears "average is above
    zero" about half the time. That is a coin flip wearing a lab coat.

    So the bar is the average beating zero by PAPER_CLV_SIGMA standard
    errors, which needs the spread of the individual bets, not just their
    mean. `clvs` is the list of per-bet info percentages (see the top of this
    docstring); the name is older than the switch from CLV to info.

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

    The cost is honest: a small edge needs a lot of evidence - roughly 454
    bets to detect a real +0.5% edge 80% of the time, which is months at
    three a day. docs/gates.md has the full table. That is the correct
    trade: passing a model with no edge loses money indefinitely, making a
    good model wait only makes it wait.

    The 50-bet floor stays as a separate, independent condition: a handful
    of lucky bets can clear a t-statistic, and n is the cheaper guard.

    For scanner strategies (scanner.gates.score) only: `metric` names what
    `clvs` holds in the reason, and `refuse`, when given, is a rule the
    caller holds this record to that sports are not. The evidence is
    recorded as usual, but it cannot pass, and the reason says why first.
    The defaults leave every sport caller exactly as it was.
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
    # per-game spread. A caller that cannot say what fraction of its settled
    # bets it managed to grade cannot show the graded ones are representative.
    cov = float(coverage) if coverage is not None else None
    covered = cov is not None and cov >= MIN_COVERAGE

    by_slot, slot_share, lopsided = {}, {}, None
    if slots is not None and len(slots) == n_bets:
        for sl, c in zip(slots, clvs):
            by_slot.setdefault(sl, []).append(c)
        slot_share = {k: len(v) / n_bets for k, v in by_slot.items()}
        if cov is None or cov < COVERAGE_WAIVES_SLOTS:
            over = [k for k, v in slot_share.items() if v > MAX_SLOT_SHARE]
            lopsided = over[0] if over else None

    # A placebo bets a RANDOM side through the same pipeline. It should score
    # nothing. If it clears the same bar, whatever this gate is measuring is
    # not the model - a systematic line drift, a bug in the decomposition, a
    # selection effect in which bets get graded. Any of those would make a
    # passing real result meaningless, so the gate refuses.
    placebo_stat = None
    if placebo:
        p_mean = sum(placebo) / len(placebo)
        p_se = _stdev(placebo) / (len(placebo) ** 0.5) if len(placebo) > 1 else 0.0
        placebo_passes = (len(placebo) >= MIN_PAPER_BETS
                          and p_mean - PAPER_CLV_SIGMA * p_se > 0)
        placebo_stat = {"n": len(placebo), "mean": round(p_mean, 3),
                        "would_pass": placebo_passes}
    else:
        placebo_passes = False

    passed = (enough and convincing and covered and lopsided is None
              and not placebo_passes and refuse is None)
    if not enough:
        reason = f"only {n_bets} graded paper bets, need {MIN_PAPER_BETS}"
    elif avg_clv <= 0:
        reason = f"mean {metric} {avg_clv:+.2f}% over {n_bets} bets is not positive"
    elif not convincing:
        reason = (f"mean {metric} {avg_clv:+.2f}% over {n_bets} bets is within noise "
                  f"(SE {se:.2f}%, needs to clear {PAPER_CLV_SIGMA:g} SE; t={t:.2f})")
    elif cov is None:
        reason = (f"mean {metric} {avg_clv:+.2f}% over {n_bets} bets clears the noise, "
                  f"but coverage was not supplied, so the graded bets cannot be "
                  f"shown to represent the bets actually placed")
    elif not covered:
        reason = (f"COVERAGE is the blocker: only {cov:.0%} of settled paper bets "
                  f"could be graded (need {MIN_COVERAGE:.0%}). The graded ones are "
                  f"whichever games a cron happened to land near, not a random "
                  f"sample. Fix pre-game collection before reading anything into "
                  f"the {avg_clv:+.2f}% {metric} over {n_bets} bets")
    elif placebo_passes:
        reason = (f"mean {metric} {avg_clv:+.2f}% over {n_bets} bets clears the bar, "
                  f"but SO DOES A RANDOM-SIDE PLACEBO "
                  f"({placebo_stat['mean']:+.2f}% over {placebo_stat['n']}) - "
                  f"whatever this is measuring, it is not the model")
    elif lopsided is not None:
        reason = (f"mean {metric} {avg_clv:+.2f}% over {n_bets} bets clears the noise "
                  f"at {cov:.0%} coverage, but {slot_share[lopsided]:.0%} of them "
                  f"are '{lopsided}' games (max {MAX_SLOT_SHARE:.0%}) - that "
                  f"validates a slate slot, not the model")
    else:
        reason = (f"mean {metric} {avg_clv:+.2f}% over {n_bets} bets, {t:.1f} SE "
                  f"above zero, at {cov:.0%} coverage")
    if refuse is not None:
        reason = f"{refuse}. Measured: {reason}"

    data = _load()
    entry = data.setdefault(sport, {})
    new_paper = {"passed": passed, "n_bets": int(n_bets),
                 "avg_clv": round(float(avg_clv), 3),
                 "sd_clv": round(sd, 3), "se_clv": round(se, 4),
                 "t_stat": round(t, 3) if t != float("inf") else None,
                 "placebo": placebo_stat,
                 "coverage": None if cov is None else round(cov, 4),
                 "slot_share": {k: round(v, 3) for k, v in slot_share.items()},
                 # info per slot, so a model that only works on late games is
                 # visible rather than averaged away.
                 "info_by_slot": {k: round(sum(v) / len(v), 3)
                                  for k, v in by_slot.items()},
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
    measured gates already pass, so it cannot be used to skip them - and,
    for a scanner strategy, unless the strategy registered under that name
    now is the definition its gates were recorded for.
    """
    g = gates(sport)
    if not g["walk_forward"]:
        return f"REFUSED - gate 1 not passed. {explain(sport)}"
    if not g["paper_trading"]:
        paper = (status(sport) or {}).get("paper_trading") or {}
        return ("REFUSED - gate 2 not passed: "
                + paper.get("reason", "no paper trading recorded"))
    data = _load()
    if data[sport].get("kind") == "strategy":
        # Its file edited in place - a new experiment or metric under the
        # same name - the gates on record are the old definition's
        # (re-verification 2026-09-25). A sport's block never gets here.
        from scanner import gates as strategy_gates
        changed = strategy_gates.redefined(sport, data[sport])
        if changed:
            return f"REFUSED - {changed}"
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
    gate1 = "backtest" if s.get("kind") == "strategy" else "walk-forward"
    bits = [
        gate1 + (" PASS" if g["walk_forward"]
                 else " FAIL (" + str(s.get("reason", "?")) + ")"),
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


def only_paper_changed(committed: dict, local: dict) -> bool:
    """True when discarding `local` for `committed` can only fail closed.

    The scheduled job runs in a checkout that must stay clean, and
    bets/paper.score() rewrites this file every time a paper bet settles. That
    gate-2 block is recomputed from the database on every run, so throwing the
    local copy away before a pull loses nothing. Anything else in the file is a
    decision - a gate-1 record, or a human's arm() - and must never be
    discarded silently, so this says no whenever anything else differs, or
    whenever the committed copy would claim a gate-2 pass the local one does
    not.
    """
    def rest(d):
        out = {k: v for k, v in (d or {}).items() if k != "paper_trading"}
        if out.get("armed") is False:
            out.pop("armed")        # record_paper writes armed=False on a fail
        return out

    for sport in set(committed) | set(local):
        c, l = committed.get(sport) or {}, local.get(sport) or {}
        if rest(c) != rest(l):
            return False
        c_pass = bool((c.get("paper_trading") or {}).get("passed"))
        l_pass = bool((l.get("paper_trading") or {}).get("passed"))
        if c_pass and not l_pass:
            return False
    return True


def _committed_copy() -> dict | None:
    """validation.json as the current git commit has it. None if unreadable."""
    import subprocess
    try:
        r = subprocess.run(["git", "show", "HEAD:validation.json"],
                           capture_output=True, text=True, encoding="utf-8",
                           cwd=PATH.parent, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


if __name__ == "__main__":
    import sys
    if "--only-paper-changed" in sys.argv:
        # Exit 0: the only local change is the recomputable gate-2 block, so
        # scheduled_check.bat may discard it before pulling. Exit 1: keep it,
        # and let the dirty-tree guard refuse.
        committed = _committed_copy()
        raise SystemExit(0 if committed is not None
                         and only_paper_changed(committed, _load()) else 1)
    print(report())
