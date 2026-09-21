"""One-command daily pipeline, all active in-season sports.

  python run_daily.py morning  -> schedules + opening odds + features
  python run_daily.py close    -> closing odds (CLV anchor)
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
    print("Morning run complete. Review flagged edges before betting.")


def close():
    odds.pull("close")
    print("Closing snapshots captured (CLV anchor).")


def grade():
    scores.pull_all()
    if "mlb" in active_sports(dt.date.today().month):
        mlb.pull_day()
    betlog.review()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    {"morning": morning, "close": close, "grade": grade}[mode]()
