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

Two rules follow, and both are enforced here:

  1. Clearing requires beating a REAL de-vigged market. Beating a placeholder
     (a home-constant, a fixed 54%) clears nothing, however big the margin.
  2. No record means not cleared. This fails closed: a fresh checkout, a cloud
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


def _load() -> dict:
    if not PATH.exists():
        return {}
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def record(sport: str, baseline_kind: str, seasons: list[dict]) -> dict:
    """Persist a walk-forward result and decide whether the sport is cleared.

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

    entry = {"recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "baseline_kind": baseline_kind, "cleared": cleared,
             "reason": reason, "seasons": rows}
    data = _load()
    data[sport] = entry
    PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return entry


def status(sport: str) -> dict | None:
    return _load().get(sport)


def is_cleared(sport: str) -> bool:
    """True only if a recorded walk-forward beat a real market every season."""
    s = status(sport)
    return bool(s and s.get("cleared"))


def explain(sport: str) -> str:
    s = status(sport)
    if s is None:
        return (f"{sport}: NOT CLEARED - no walk-forward result recorded. "
                f"Run the sport's build_training script.")
    verdict = "CLEARED" if s["cleared"] else "NOT CLEARED"
    return f"{sport}: {verdict} - {s['reason']} (recorded {s['recorded_at']})"


def report() -> str:
    data = _load()
    if not data:
        return "No sports validated yet. Nothing is cleared to bet."
    return "\n".join(explain(s) for s in sorted(data))


if __name__ == "__main__":
    print(report())
