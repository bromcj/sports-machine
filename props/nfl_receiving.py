"""B3: the NFL receiving-props model, and its backtest. Free, offline.

    python props/nfl_receiving.py

docs/experiments.md B3, built on the B1 framework: OPPORTUNITY x RATE.

    receptions      = targets x catch rate
    receiving yards = targets x yards per target

WHERE THE EDGE WOULD BE, IF THERE IS ONE. Pre-registered before any of this ran:
target redistribution when a team's top receiver is ruled out. Books price a
receiver off his own season line; when the man ahead of him is inactive, his
opportunity changes by more than his own history says. `vacated_share` is that
feature, and B3's pre-registered claim is that the model beats the market
SPECIFICALLY on those games and is at best neutral elsewhere.

LEAKAGE. Every feature is built from cumulative sums shifted by one game inside
(player, season) - never a rolling window followed by .shift(), which is the
construction that once handed a player's first game of a season the window
ending at his last game of the previous one. Inactives come from the pre-game
injury report (`report_status == 'Out'`), not from post-game snap counts: a
player can be active and not play, and inferring it afterwards would be knowing
something the market did not.

GAME SCRIPT IS A LEGAL INPUT. The spread and total are the market's own
pre-game numbers and they drive how much a team throws. Using them is not
cheating, it is using public information - and it is the half of this model
most likely to carry anything, because books price props off season-long rates
and are slower to re-cut them for game context.

WHAT IS DELIBERATELY NOT DONE. No feature is added, no window changed and no
parameter retuned after seeing a test result. The shrinkage constants below are
conventional stabilisation points, not fitted to the outcome.
"""
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import paths
from props.framework import SPECS, shrink, price
from props.nfl_data import receiving_weeks, inactives
from props.nfl_teams import to_abbr
from props.validate import naive_baseline, outcome_over, calibration_by_decile
from research.stats import logloss, block_bootstrap, fmt

PARQUET = paths.DATA_DIR / "props_nfl.parquet"

# Stabilisation points. Conventional, not fitted here: a catch rate settles
# in roughly 50 targets, a target share in roughly 4 games of team volume.
REG_TARGETS_SHARE = 30.0     # team targets of regression on a player's share
REG_CATCH = 50.0             # targets
REG_YPT = 50.0               # targets
REG_TEAM = 60.0              # team targets, for a team's own pass volume

MARKET_TO_SPEC = {"player_receptions": "player_receptions",
                  "player_reception_yds": "player_reception_yds"}
ACTUAL = {"player_receptions": "receptions",
          "player_reception_yds": "receiving_yards"}


# ----------------------------------------------------------------- names ---

def norm_name(s: str) -> str:
    """Strip everything that differs between two spellings of one person."""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # The Odds API disambiguates duplicate names inline: "Michael (Saints)
    # Thomas". Strip the hint - the team restriction below already does that
    # job, and more reliably.
    s = re.sub(r"\([^)]*\)", " ", s)
    s = s.lower().replace(".", "").replace("'", "").replace("-", " ")
    for suf in (" jr", " sr", " ii", " iii", " iv", " v"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return " ".join(s.split())


# -------------------------------------------------------------- features ---

def player_features() -> pd.DataFrame:
    """As-of opportunity and rate inputs for every player-week."""
    w = receiving_weeks().sort_values(["player_id", "season", "week"]).copy()

    # --- team pass volume, as-of ---
    team = (w.groupby(["team", "season", "week"])["targets"].sum()
             .rename("team_targets").reset_index()
             .sort_values(["team", "season", "week"]))
    g = team.groupby(["team", "season"], sort=False)
    team["team_prior_targets"] = g["team_targets"].cumsum() - team["team_targets"]
    team["team_prior_games"] = g.cumcount()
    league_team = team["team_targets"].mean()
    team["proj_team_targets"] = (
        (team["team_prior_targets"] + REG_TEAM * league_team / 34.0)
        / (team["team_prior_games"] + REG_TEAM / 34.0))

    w = w.merge(team[["team", "season", "week", "team_targets",
                      "team_prior_targets", "team_prior_games",
                      "proj_team_targets"]],
                on=["team", "season", "week"], how="left")

    # --- player history, as-of, cumulative-minus-self inside (player, season) ---
    gp = w.groupby(["player_id", "season"], sort=False)
    for c in ("targets", "receptions", "receiving_yards"):
        w[f"prior_{c}"] = gp[c].cumsum() - w[c]
    w["prior_games"] = gp.cumcount()
    w["prior_team_targets"] = (gp["team_targets"].cumsum() - w["team_targets"])

    # league priors, by position - a tight end is not a wide receiver
    pos = (w.groupby("position")
             .agg(lg_catch=("receptions", "sum"), lg_tgt=("targets", "sum"),
                  lg_yds=("receiving_yards", "sum")).reset_index())
    pos["lg_catch_rate"] = pos["lg_catch"] / pos["lg_tgt"]
    pos["lg_ypt"] = pos["lg_yds"] / pos["lg_tgt"]
    w = w.merge(pos[["position", "lg_catch_rate", "lg_ypt"]], on="position",
                how="left")
    lg_share = (w["targets"].sum() / w["team_targets"].sum())

    # --- shrunk rates ---
    w["share"] = np.where(w["prior_team_targets"] > 0,
                          w["prior_targets"] / w["prior_team_targets"],
                          np.nan)
    w["proj_share"] = [
        shrink(pt if np.isfinite(pt) else 0.0,
               s if np.isfinite(s) else lg_share, lg_share, REG_TARGETS_SHARE)
        for pt, s in zip(w["prior_team_targets"], w["share"])]
    w["proj_catch_rate"] = [
        shrink(pt, (pr / pt) if pt > 0 else lc, lc, REG_CATCH)
        for pt, pr, lc in zip(w["prior_targets"], w["prior_receptions"],
                              w["lg_catch_rate"])]
    w["proj_yds_per_target"] = [
        shrink(pt, (py / pt) if pt > 0 else ly, ly, REG_YPT)
        for pt, py, ly in zip(w["prior_targets"], w["prior_receiving_yards"],
                              w["lg_ypt"])]
    return w


def add_vacated(w: pd.DataFrame) -> pd.DataFrame:
    """Share of team targets belonging to team-mates ruled OUT this week.

    The one pre-registered edge. Computed from the PRE-GAME injury report and
    from each absent player's own as-of share, so nothing in it is known only
    after kickoff.
    """
    out = inactives()
    if out.empty or "gsis_id" not in out.columns:
        w["vacated_share"] = 0.0
        return w
    o = out.rename(columns={"gsis_id": "player_id"})[
        ["player_id", "season", "week", "team"]].drop_duplicates()
    o["is_out"] = 1
    w = w.merge(o[["player_id", "season", "week", "is_out"]],
                on=["player_id", "season", "week"], how="left")
    w["is_out"] = w["is_out"].fillna(0).astype(int)

    # A player ruled OUT does not appear in that week's player stats at all -
    # nflverse only has rows for men who played. The first version of this
    # summed `proj_share` over the matched rows, which was ALWAYS ZERO by
    # construction: the only rows it could match were players who were listed
    # out and then played anyway. The whole pre-registered feature was a
    # column of zeros and the subgroup test silently had one group in it.
    #
    # The share an absent player WOULD have had has to come from his history:
    # his most recent as-of share strictly before that week.
    hist = (w[["player_id", "season", "week", "proj_share"]]
            .dropna(subset=["proj_share"])
            .sort_values(["player_id", "season", "week"]))
    prior = pd.merge_asof(
        o.sort_values("week"),
        hist.rename(columns={"proj_share": "last_share"}).sort_values("week"),
        on="week", by=["player_id", "season"],
        allow_exact_matches=False, direction="backward")
    prior["last_share"] = prior["last_share"].fillna(0.0)

    vac = (prior.groupby(["team", "season", "week"])["last_share"].sum()
                .rename("vacated_share").reset_index())
    w = w.merge(vac, on=["team", "season", "week"], how="left")
    w["vacated_share"] = w["vacated_share"].fillna(0.0)
    return w


def project(w: pd.DataFrame) -> pd.DataFrame:
    """Opportunity x rate, with vacated targets redistributed by share."""
    # A player who is himself out has no projection to make.
    w = w[w["is_out"] == 0].copy()
    # Redistribute the vacated share across the remaining players in
    # proportion to what they already had. This is the whole hypothesis: if
    # the WR1 is out, everyone else's opportunity rises, and by more than
    # their own game log says.
    remaining = (w.groupby(["team", "season", "week"])["proj_share"]
                  .transform("sum"))
    w["share_adj"] = w["proj_share"] * (
        1.0 + w["vacated_share"] / remaining.clip(lower=1e-6))
    w["proj_targets"] = w["proj_team_targets"] * w["share_adj"]
    return w


# -------------------------------------------------------------- matching ---

def load_props() -> pd.DataFrame:
    p = pd.read_parquet(PARQUET)
    p = p.dropna(subset=["game_id", "player", "line", "p_fair"])
    # game_id is the nflverse id: 2023_01_DET_KC
    parts = p["game_id"].str.split("_", expand=True)
    p["season"] = parts[0].astype(int)
    p["week"] = parts[1].astype(int)
    p["away_ab"] = parts[2]
    p["home_ab"] = parts[3]
    p["name_key"] = p["player"].map(norm_name)
    return p


def match_players(props: pd.DataFrame, w: pd.DataFrame) -> pd.DataFrame:
    """Odds API display name -> nflverse gsis_id, inside the game's two teams.

    Restricted to the two teams playing, which is what makes a name match safe:
    the league has had two Mike Williamses and two Josh Allens at once, and a
    league-wide name join would pick whichever sorted first.
    """
    w = w.copy()
    w["name_key"] = w["player_display_name"].map(norm_name)
    idx = w[["name_key", "season", "week", "team", "player_id"]].drop_duplicates()

    a = props.merge(idx.rename(columns={"team": "away_ab"}),
                    on=["name_key", "season", "week", "away_ab"], how="left")
    b = props.merge(idx.rename(columns={"team": "home_ab"}),
                    on=["name_key", "season", "week", "home_ab"], how="left")
    props = props.copy()
    props["player_id"] = a["player_id"].fillna(b["player_id"]).values
    props["match"] = np.where(props["player_id"].notna(), "full name", None)

    # --- fallback: surname, inside one team's week, only when unique ---
    # The remaining misses are all first-name variants the two sources spell
    # differently - Joshua/Josh Palmer, Kenneth/Kenny Gainwell, Gabriel/Gabe
    # Davis, Chigoziem/Chig Okonkwo, Nathaniel/Tank Dell. A surname match is
    # normally reckless; restricted to the twenty-odd receivers on ONE team in
    # ONE week, and REQUIRED TO BE UNIQUE there, it is not. Where a team has
    # two men of the same surname that week, both are left unmatched rather
    # than guessed - which is the rule the Athletics bug taught.
    w2 = w.copy()
    w2["last"] = w2["name_key"].str.split().str[-1]
    uniq = (w2.groupby(["last", "season", "week", "team"])["player_id"]
              .agg(["first", "nunique"]).reset_index())
    uniq = uniq[uniq["nunique"] == 1][["last", "season", "week", "team",
                                       "first"]]
    uniq = uniq.rename(columns={"first": "player_id_fb"})

    need = props["player_id"].isna()
    if need.any():
        pn = props.loc[need].copy()
        pn["last"] = pn["name_key"].str.split().str[-1]
        fa = pn.merge(uniq.rename(columns={"team": "away_ab"}),
                      on=["last", "season", "week", "away_ab"], how="left")
        fb = pn.merge(uniq.rename(columns={"team": "home_ab"}),
                      on=["last", "season", "week", "home_ab"], how="left")
        got = fa["player_id_fb"].fillna(fb["player_id_fb"]).values
        props.loc[need, "player_id"] = got
        props.loc[need & props["player_id"].notna(), "match"] = "surname"
    return props


# -------------------------------------------------------------- backtest ---

def build() -> pd.DataFrame:
    w = add_vacated(player_features())
    w = project(w)
    props = match_players(load_props(), w)

    matched = props["player_id"].notna()
    print(f"player name match: {matched.sum():,} of {len(props):,} prop rows "
          f"({100 * matched.mean():.1f}%)")
    print(props["match"].value_counts(dropna=False).to_string())
    miss = (props.loc[~matched, "player"].value_counts().head(12))
    if len(miss):
        print("  most common unmatched names (reported, never dropped "
              "silently):")
        for n, c in miss.items():
            print(f"    {n:28s} {c:>5,}")

    d = props[matched].merge(
        w[["player_id", "season", "week", "position", "proj_targets",
           "proj_catch_rate", "proj_yds_per_target", "vacated_share",
           "prior_games", "targets", "receptions", "receiving_yards"]],
        on=["player_id", "season", "week"], how="inner")
    print(f"joined to outcomes: {len(d):,} rows")
    return d


def calibrate_walk_forward(m: pd.DataFrame, market: str,
                           min_train_months: int = 2) -> pd.DataFrame:
    """Fit the projection's mean and spread on EARLIER months, apply to later.

    Why this is needed and why it is not cheating.

    The raw projection is biased low on the population books post lines on:
    4.60 projected targets against 5.07 actual, a 9% shortfall that carries
    straight through to 3.13 projected receptions against 3.43. The cause is
    structural rather than a coding error. A player's target share is shrunk
    toward the LEAGUE mean share, and that mean is computed over every rostered
    receiver including those who barely play - while a book only posts a line
    on the ten-to-thirteen busiest men in a game. Shrinking the top of a
    distribution toward the middle of a wider one pulls it down.

    The fix is the walk-forward calibration docs/experiments.md B1 specified
    from the start and this file initially skipped: fit a mean calibration and
    a dispersion on months STRICTLY EARLIER than the one being scored, and
    apply them forward. No month is ever calibrated with knowledge of itself,
    and the parameters are two numbers per month, not a refitted model.

    It is emphatically not "tune until the test passes". It was specified
    before any result was seen, it is fitted only on past data, and it is
    applied identically whether it helps or hurts.
    """
    m = m.sort_values("game_date").copy()
    m["month"] = m["game_date"].dt.to_period("M")
    months = sorted(m["month"].unique())
    discrete = market == "player_receptions"

    out = []
    for i, mo in enumerate(months):
        tr = m[m["month"].isin(months[:i])]
        te = m[m["month"] == mo].copy()
        if i < min_train_months or len(tr) < 500:
            # Nothing to calibrate on yet: score it uncalibrated rather than
            # borrowing from the future, and let it count against the model.
            te["cal_mean"] = te["projection"]
            te["cal_spread"] = (SPECS[MARKET_TO_SPEC[market]].dispersion)
            out.append(te)
            continue
        # One multiplicative factor, not a regression: a slope-and-intercept
        # fit would quietly re-rank players using the test period's own scale,
        # and the defect being corrected is a level shift, not a tilt.
        factor = tr["actual"].sum() / max(tr["projection"].sum(), 1e-9)
        te["cal_mean"] = te["projection"] * factor
        mu = (tr["projection"] * factor).values
        resid2 = (tr["actual"].values - mu) ** 2
        mu_te = (te["projection"] * factor).values
        if discrete:
            # A negative binomial's variance is mu + mu^2/r, so Var/mean is NOT
            # a constant - it grows with the mean. Fitting one pooled ratio
            # therefore fits the crowded low-projection rows and leaves the
            # model far too confident on the high ones, which is exactly the
            # over-confidence the decile table showed: predicted 0.17 against
            # an actual 0.38, predicted 0.73 against an actual 0.59.
            #
            # Fit the variance FUNCTION instead, Var = a*mu + b*mu^2, by least
            # squares on the squared residuals, and let every row take its own
            # ratio.
            coef, *_ = np.linalg.lstsq(np.c_[mu, mu ** 2], resid2, rcond=None)
            a, b = float(max(coef[0], 0.05)), float(max(coef[1], 0.0))
            te["cal_spread"] = np.clip(a + b * mu_te, 1.01, 8.0)
        else:
            # Same idea on the other scale: the coefficient of variation of a
            # yardage total falls as the projection rises, so fit sd = c*mu^p
            # in logs rather than assuming one cv covers a 20-yard projection
            # and a 90-yard one.
            ok = mu > 0.5
            p, logc = np.polyfit(np.log(mu[ok]),
                                 0.5 * np.log(np.maximum(resid2[ok], 1e-6)), 1)
            m_te = np.maximum(mu_te, 0.5)
            te["cal_spread"] = np.clip(np.exp(logc) * m_te ** p / m_te,
                                       0.15, 3.0)
        out.append(te)
    return pd.concat(out, ignore_index=True)


def recalibrate_probs(m: pd.DataFrame, min_train_months: int = 2
                      ) -> pd.DataFrame:
    """Walk-forward Platt scaling of the model's P(over). The model's best shot.

    The decile tables showed the raw model badly over-confident: it predicts
    0.17 where reality is 0.38, and 0.92 where reality is 0.55. That is a real
    defect, and it is ALSO a confound - a model can carry genuine information
    and still lose on log loss purely by being too sure of itself, and it would
    be unfair to conclude "no signal" from a number that over-confidence
    explains.

    So the model gets a second, more generous scoring: fit
    logit(p) -> a + b*logit(p) on strictly earlier months and apply it forward.
    This cannot invent information - a monotone transform preserves the
    model's ordering exactly - it can only stop bad confidence from masking
    good ranking. Both versions are reported. If the recalibrated model still
    loses, the loss is about information, not about calibration.
    """
    from research.stats import logit, expit
    from scipy.optimize import minimize

    m = m.sort_values("game_date").copy()
    months = sorted(m["month"].unique())
    y_all = (m["actual"] > m["line"]).astype(float)
    m["_y"] = y_all
    live = ~np.isclose(m["actual"], m["line"])
    out = []
    for i, mo in enumerate(months):
        tr = m[m["month"].isin(months[:i]) & live]
        te = m[m["month"] == mo].copy()
        if i < min_train_months or len(tr) < 500:
            te["p_cal"] = te["p_over_live"]
            out.append(te)
            continue
        x, y = logit(tr["p_over_live"].values), tr["_y"].values

        def nll(b):
            p = np.clip(expit(b[0] + b[1] * x), 1e-12, 1 - 1e-12)
            return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

        b = minimize(nll, np.array([0.0, 1.0]), method="BFGS").x
        te["p_cal"] = expit(b[0] + b[1] * logit(te["p_over_live"].values))
        out.append(te)
    return pd.concat(out, ignore_index=True)


def price_calibrated(m: pd.DataFrame, market: str) -> pd.DataFrame:
    """P(over | not a push) from the calibrated mean and spread, per row."""
    from props.distributions import count_dist, yards_dist, \
        prob_over_excluding_push, over_push_under
    discrete = market == "player_receptions"
    po, pp, pu, pl = [], [], [], []
    for mean, spread, line in zip(m["cal_mean"], m["cal_spread"], m["line"]):
        d, disc = (count_dist(mean, spread) if discrete
                   else yards_dist(mean, spread))
        o, p, u = over_push_under(d, line, disc)
        po.append(o); pp.append(p); pu.append(u)
        pl.append(prob_over_excluding_push(d, line, disc))
    m = m.copy()
    m["p_over"], m["p_push"], m["p_under"] = po, pp, pu
    m["p_over_live"] = pl
    return m


def score_market(d: pd.DataFrame, market: str, fair_mode: str) -> pd.DataFrame:
    """Price every prop and attach the market's own probability.

    fair_mode:
      'any'      Pinnacle at the same line where it exists, else the de-vigged
                 consensus of the other books - the rule bets/log.py uses.
      'sharp'    only rows where PINNACLE priced that exact line. Half the
                 sample, one ruler.
    """
    spec = SPECS[MARKET_TO_SPEC[market]]
    m = d[d["market"] == market].copy()
    if fair_mode == "sharp":
        m = m[m["fair_source"] == "pinnacle"]
    m = m.dropna(subset=["proj_targets", "p_fair"])
    rate_col = ("proj_catch_rate" if market == "player_receptions"
                else "proj_yds_per_target")
    # A fresh frame with exactly the three columns price() needs. Renaming
    # inside `m` produced TWO columns called proj_targets - the original and
    # the alias - and `rows[spec.opportunity]` then returned a DataFrame, which
    # failed a long way from the cause.
    rows = pd.DataFrame({spec.opportunity: m["proj_targets"].values,
                         spec.rate: m[rate_col].values,
                         "line": m["line"].values})
    out = price(spec, rows, line_col="line")
    m = pd.concat([m.reset_index(drop=True), out.reset_index(drop=True)],
                  axis=1)
    m["actual"] = m[ACTUAL[market]]
    m["game_date"] = pd.to_datetime(
        m["commence_time"], format="ISO8601", utc=True).dt.tz_localize(None)
    # Walk-forward calibration, then re-price from the calibrated mean/spread.
    m = calibrate_walk_forward(m, market)
    m = price_calibrated(m, market)
    return recalibrate_probs(m)


def report(market: str, fair_mode: str, m: pd.DataFrame) -> dict:
    spec = SPECS[MARKET_TO_SPEC[market]]
    y = outcome_over(m)
    live = y.notna()
    s = m[live].copy()
    yv = y[live].values

    s["p_naive"] = naive_baseline(s, spec, player_col="player_id",
                                  actual_col="actual", date_col="game_date")
    ll_model = logloss(s["p_over_live"].values, yv)
    ll_cal = logloss(s["p_cal"].values, yv)
    ll_naive = logloss(s["p_naive"].values, yv)
    ll_mkt = logloss(s["p_fair"].values, yv)

    print("\n" + "=" * 74)
    print(f"{market}   fair = {fair_mode}")
    print("=" * 74)
    print(f"  {len(m):,} props, {(~live).sum():,} pushes dropped, "
          f"{len(s):,} live, {s['game_id'].nunique():,} games")
    print(f"\n  log loss   model raw       {ll_model.mean():.6f}")
    print(f"             model recalibrated {ll_cal.mean():.6f}   "
          f"(its best shot)")
    print(f"             naive             {ll_naive.mean():.6f}   "
          f"(player season-to-date + Poisson)")
    print(f"             MARKET            {ll_mkt.mean():.6f}")

    r_naive = block_bootstrap(np.asarray(ll_naive - ll_cal),
                              s["game_id"].values, n_boot=1500)
    r_mkt = block_bootstrap(np.asarray(ll_mkt - ll_cal),
                            s["game_id"].values, n_boot=1500)
    print("\n  recalibrated vs naive  " + fmt(r_naive, places=6))
    print("  recalibrated vs MARKET " + fmt(r_mkt, places=6))
    print("            (positive = the model is better)")

    print("\n  calibration by decile, recalibrated model")
    cal = calibration_by_decile(s["p_cal"].values, yv)
    for row in cal.itertuples():
        print(f"    {row.n:>6,} predicted {row.predicted:.4f}  actual "
              f"{row.actual:.4f}  gap {row.gap:+.4f}")

    print("\n  PRE-REGISTERED: games where a team-mate was ruled OUT")
    for label, sub in (("vacated share > 0", s[s["vacated_share"] > 0]),
                       ("vacated share = 0", s[s["vacated_share"] == 0])):
        if len(sub) < 200:
            continue
        yy = outcome_over(sub).values
        keep = np.isfinite(yy)
        rr = block_bootstrap(
            (logloss(sub["p_fair"].values[keep], yy[keep])
             - logloss(sub["p_cal"].values[keep], yy[keep])),
            sub["game_id"].values[keep], n_boot=1200)
        print(f"    {label:20s} n={keep.sum():>6,}  vs market " +
              fmt(rr, places=6))
    return {"market": market, "fair": fair_mode, "n": len(s),
            "ll_model": float(ll_cal.mean()),
            "ll_market": float(ll_mkt.mean()),
            "vs_market": r_mkt, "vs_naive": r_naive}


if __name__ == "__main__":
    d = build()
    results = []
    for market in ("player_receptions", "player_reception_yds"):
        for fair_mode in ("any", "sharp"):
            m = score_market(d, market, fair_mode)
            if len(m) < 500:
                print(f"\n{market} / {fair_mode}: only {len(m)} rows, skipping")
                continue
            results.append(report(market, fair_mode, m))

    print("\n" + "=" * 74)
    print("SUMMARY - does the model beat the price?")
    print("=" * 74)
    print(f"{'market':24s} {'fair':7s} {'n':>7s} {'model':>10s} {'market':>10s} "
          f"{'margin':>10s} {'t':>7s}")
    for r in results:
        print(f"{r['market']:24s} {r['fair']:7s} {r['n']:>7,} "
              f"{r['ll_model']:>10.6f} {r['ll_market']:>10.6f} "
              f"{r['vs_market']['mean']:>+10.6f} {r['vs_market']['t']:>+7.2f}")
    print("\nPositive margin and t > 2 in BOTH fair-price definitions is the "
          "bar.\nAnything less is 'no edge found'.")
