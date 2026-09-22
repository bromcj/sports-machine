"""One-command daily pipeline, all active in-season sports.

  python run_daily.py morning  -> schedules + odds + features + predictions
  python run_daily.py close    -> closing odds (CLV anchor)
  python run_daily.py picks    -> today's model vs market, no pulls, free
  python run_daily.py grade    -> finals + review
"""
import sys
import datetime as dt
from ingest import mlb, odds, scores
from config import active_sports
from bets import log as betlog


def morning():
    month = dt.date.today().month
    live = active_sports(month)
    print(f"In-season sports today: {', '.join(live) or 'none'}")
    if "mlb" in live:
        mlb.pull_day()          # probables come from MLB Stats API
    scores.pull_all()           # schedules/finals for everything else
    odds.pull("open")
    if "mlb" in live:
        # Features and predictions need no API credits - they read data already
        # on disk. Skipped quietly if the model has not been trained yet.
        try:
            from features.build import build_for_date
            from model.predict import predict_for_date
            if build_for_date():
                predict_for_date()
        except FileNotFoundError as e:
            print(f"(no predictions: {e})")
    print("Morning run complete. Run `python run_daily.py picks` to see them.")


def close():
    odds.pull("close")
    print("Closing snapshots captured (CLV anchor).")


def show_picks():
    """Model vs market for today. Reads the database only - costs nothing."""
    from model.predict import picks
    picks()


def grade():
    scores.pull_all()
    if "mlb" in active_sports(dt.date.today().month):
        mlb.pull_day()
    betlog.review()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    {"morning": morning, "close": close, "picks": show_picks,
     "grade": grade}[mode]()
