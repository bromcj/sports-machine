"""The shared statistics for docs/experiments.md. One implementation each.

Small on purpose. Every test in Part A and Part E reduces to the same two
questions - "is this per-game difference reliably non-zero" and "does it hold
in every season" - and the pre-registered pass rule is written against a
specific answer to the first. Having one function for it means a later result
cannot quietly use a friendlier standard error than an earlier one.

WHY BLOCK BOOTSTRAP AND NOT A t-TEST.

A paired t-test assumes the per-game differences are independent. They are not.
Every game on one date shares a day: the same weather systems, the same
national narrative, the same bad number copied across books, and - for anything
involving our model - the same day's feature build. Treating 2,200 games as
2,200 independent draws understates the standard error, which is exactly the
direction that manufactures false findings. Resampling whole days keeps
whatever correlation exists inside a day intact.

Props resample by GAME rather than by day, because the correlation that matters
there is between two receivers in one game. docs/experiments.md fixes which
blocking each experiment uses; this module just does what it is told.
"""
import numpy as np

SEED = 20260923      # fixed so a rerun reproduces the number in the write-up
N_BOOT = 10_000


def logit(p, eps=1e-9):
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def expit(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def logloss(p, y, eps=1e-9):
    """Per-observation cross-entropy. Not averaged: the pairing happens first."""
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def block_bootstrap(values, blocks, n_boot=N_BOOT, seed=SEED, stat=np.mean):
    """Resample whole blocks with replacement. Returns mean, se, lo, hi, t.

    `values` is one number per observation, `blocks` the block label for each
    (a date, or a game id). Blocks are resampled to the same COUNT as observed,
    not to the same number of observations, which is the standard cluster
    bootstrap and is what keeps unequal block sizes honest.
    """
    values = np.asarray(values, dtype=float)
    blocks = np.asarray(blocks)
    ok = np.isfinite(values)
    values, blocks = values[ok], blocks[ok]
    if len(values) == 0:
        return dict(mean=np.nan, se=np.nan, lo=np.nan, hi=np.nan, t=np.nan,
                    n=0, n_blocks=0)

    uniq, inv = np.unique(blocks, return_inverse=True)
    order = np.argsort(inv, kind="stable")
    grouped = values[order]
    bounds = np.searchsorted(inv[order], np.arange(len(uniq) + 1))
    chunks = [grouped[bounds[i]:bounds[i + 1]] for i in range(len(uniq))]

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    boot = np.empty(n_boot)
    for i in range(n_boot):
        boot[i] = stat(np.concatenate([chunks[j] for j in draws[i]]))

    point = float(stat(values))
    se = float(np.std(boot, ddof=1))
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(mean=point, se=se, lo=float(lo), hi=float(hi),
                t=point / se if se > 0 else np.nan,
                n=int(len(values)), n_blocks=int(len(uniq)))


def leave_one_out(values, blocks, groups, n_boot=2000, seed=SEED):
    """Drop each group (a season) in turn; bootstrap what is left.

    The pre-registered rule is that a result survives leave-one-season-out. The
    point is not that the remaining estimate stays significant by luck of the
    larger sample, it is that no single season is carrying the whole effect.
    """
    values, blocks, groups = (np.asarray(values, dtype=float),
                              np.asarray(blocks), np.asarray(groups))
    out = {}
    for g in np.unique(groups):
        keep = groups != g
        out[g] = block_bootstrap(values[keep], blocks[keep],
                                 n_boot=n_boot, seed=seed)
    return out


def fmt(r: dict, scale: float = 1.0, places: int = 5) -> str:
    """One-line rendering of a bootstrap result, used in every report."""
    if not np.isfinite(r.get("mean", np.nan)):
        return "  (no data)"
    return (f"{r['mean'] * scale:+.{places}f}  "
            f"[{r['lo'] * scale:+.{places}f}, {r['hi'] * scale:+.{places}f}]  "
            f"t={r['t']:+.2f}  n={r['n']:,}")
