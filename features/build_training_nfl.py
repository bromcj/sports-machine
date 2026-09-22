"""Assemble the NFL training table and run walk-forward validation.

  python features/build_training_nfl.py

Ridge on point differential; margin -> win prob via logistic k fit on
TRAIN folds only. Two baselines per test season:
  logloss_home    dumb home-constant baseline (the qualifying round)
  logloss_market  REAL de-vigged closing moneylines (the title fight)
Beating home is required; the gap to market is the honest scoreboard.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import log_loss, mean_squared_error

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from features.sports.nfl_features_v1 import build_table, FEATURE_COLUMNS
from model.calibrate import fit_k
from model.validation import record, explain, REAL_MARKET

ALPHAS = [0.1, 1.0, 10.0, 50.0, 100.0]
FIRST_TEST_SEASON = 2022          # train needs >=2 prior full seasons
LAST_TEST_SEASON = 2025           # 2026 is partial; excluded from testing


def main():
    df = build_table()
    n0 = len(df)
    df = df.dropna(subset=FEATURE_COLUMNS + ["point_diff", "novig_home_prob"])
    # A tie has point_diff == 0, and home_won = (point_diff > 0) scores it as an
    # AWAY win. That teaches the model a loss where none happened and makes the
    # log-loss unwinnable on those rows. NFL ties are rare but real; MLB has
    # none (extra innings), so only this table needs the filter.
    ties = int((df["point_diff"] == 0).sum())
    if ties:
        df = df[df["point_diff"] != 0]
        print(f"Dropped {ties} tie(s): a draw is neither a home nor an away win.")
    print(f"{n0} regular-season games; {len(df)} with complete features+market "
          f"({n0 - len(df)} dropped).")
    df.to_parquet(ROOT / "data" / "training_nfl.parquet", index=False)
    print("Training table saved -> data/training_nfl.parquet\n")

    rows = []
    for test_season in range(FIRST_TEST_SEASON, LAST_TEST_SEASON + 1):
        train = df[df["season"] < test_season]
        test = df[df["season"] == test_season]

        inner_tr = train[train["season"] < test_season - 1]
        inner_val = train[train["season"] == test_season - 1]
        best_alpha, best_rmse = ALPHAS[0], np.inf
        for a in ALPHAS:
            m = make_pipeline(StandardScaler(), Ridge(alpha=a))
            m.fit(inner_tr[FEATURE_COLUMNS], inner_tr["point_diff"])
            rmse = mean_squared_error(
                inner_val["point_diff"],
                m.predict(inner_val[FEATURE_COLUMNS])) ** 0.5
            if rmse < best_rmse:
                best_alpha, best_rmse = a, rmse

        model = make_pipeline(StandardScaler(), Ridge(alpha=best_alpha))
        model.fit(train[FEATURE_COLUMNS], train["point_diff"])
        train_pred = model.predict(train[FEATURE_COLUMNS])
        k = fit_k(train_pred, train["home_won"].values)

        pred_margin = model.predict(test[FEATURE_COLUMNS])
        p = np.clip(1 / (1 + np.exp(-k * pred_margin)), 1e-6, 1 - 1e-6)
        y = test["home_won"].values
        mkt = np.clip(test["novig_home_prob"].values, 1e-6, 1 - 1e-6)
        home_p = np.clip(np.full_like(y, train["home_won"].mean()), 1e-6, 1 - 1e-6)

        rows.append({
            "test_season": test_season, "n": len(test),
            "alpha": best_alpha, "k": round(k, 3),
            "rmse": round(mean_squared_error(test["point_diff"], pred_margin) ** 0.5, 2),
            "logloss_model": round(log_loss(y, p), 6),
            "logloss_home": round(log_loss(y, home_p), 6),
            "logloss_market": round(log_loss(y, mkt), 6),
            "acc_model": round(float(np.mean((p > 0.5) == y)), 4),
            "acc_market": round(float(np.mean((mkt > 0.5) == y)), 4),
        })

    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    record("nfl", REAL_MARKET,
           out.rename(columns={"test_season": "season"}).to_dict("records"))
    print()
    print(explain("nfl"))
    print("\nQualifying bar: logloss_model < logloss_home (must pass).")
    print("Title fight:    logloss_model vs logloss_market (real closing lines).")
    print("Expect the market to win; the mission is closing the gap.")


if __name__ == "__main__":
    main()
