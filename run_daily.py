"""One-command daily pipeline, all active in-season sports.

  python run_daily.py morning  -> schedules + odds + features + predictions
  python run_daily.py close    -> closing odds (CLV anchor)
  python run_daily.py picks    -> today's model vs market, no pulls, free
  python run_daily.py refresh  -> top up Statcast + retrain (slow, weekly)
  python run_daily.py grade    -> finals + review
  python run_daily.py cronstatus -> did the cloud fire, and on time? free
  python run_daily.py backup     -> snapshot the database, verified. free
  python run_daily.py finals     -> yesterday's and today's results. free
  python run_daily.py predict    -> statcast top-up + today's predictions. free
  python run_daily.py paper      -> place/settle/score paper bets (gate 2). free
"""
import sys
import datetime as dt
import requests
from ingest import mlb, odds, scores
from config import active_sports
from bets import log as betlog


def morning():
    month = dt.date.today().month
    live = active_sports(month)
    print(f"In-season sports today: {', '.join(live) or 'none'}")
    if "mlb" in live:
        # Guarded because this runs BEFORE the odds pull, and the odds pull is
        # the one that cannot be made up later - prices move, and there are
        # only three chances a day. Missing probables costs today's MLB
        # predictions; an unhandled error here used to cost the prices too.
        try:
            mlb.pull_day()      # probables come from MLB Stats API
        except requests.RequestException as e:
            print(f"[mlb] probables unavailable: {e}")
    scores.pull_all()           # schedules/finals for everything else
    odds.pull("open")
    if "mlb" in live:
        predict()
    print("Morning run complete. Run `python run_daily.py picks` to see them.")


def predict():
    """Top up Statcast, build today's features, predict, place paper bets.

    FREE - no API credits. Everything here reads data already on disk plus
    odds already in the database.

    Separated from morning() because morning() runs in the CLOUD, and the
    cloud cannot do any of this: data/ is gitignored and must stay out of a
    public repo, so the runner has no Statcast file and no trained model. Its
    own log says so - "no predictions: No Statcast parquet". Gate 2 can
    therefore only ever be fed from the machine that holds data/, which is
    why the local scheduled task calls this.

    The Statcast top-up matters most - the rolling windows are only as current
    as that file, and it is the one input nothing else refreshes.
    """
    try:
        from backfill import topup_statcast
        topup_statcast(allow_full_download=False)
    except Exception as e:                      # never let this kill the run
        print(f"(statcast top-up skipped: {e})")
    try:
        from features.build import build_for_date
        from model.predict import predict_for_date
        if build_for_date():
            predict_for_date()
    except FileNotFoundError as e:
        print(f"(no predictions: {e})")


def close():
    odds.pull("close")
    print("Closing snapshots captured (CLV anchor).")


def refresh():
    """Top up Statcast and retrain from scratch. Slow; weekly is plenty.

    The rolling features must be current or they are not really 30-day windows.
    The model itself drifts far more slowly - it is a ridge fit over 10k games,
    so a few more days barely moves it - which is why morning tops up the data
    every day but retraining is a separate, deliberate step.
    """
    from backfill import topup_statcast
    topup_statcast()
    import subprocess, sys as _s
    subprocess.run([_s.executable, "features/build_training.py"], check=False)


def show_picks():
    """Model vs market for today. Reads the database only - costs nothing."""
    from model.predict import picks
    picks()


def finals(days_back: int = 1):
    """Fetch completed results for today and the previous `days_back` days.

    FREE - ESPN and the MLB Stats API, no odds credits.

    Exists because finals for a game that ends at 10pm were never collected by
    anything. Both score pulls asked only for TODAY: the cloud's morning run
    fires at 10:13am, hours before any game that day finishes, and by the time
    yesterday's games were over nothing ever asked about them again. Measured
    across every archived games file the cloud produced: ESPN rows reached
    'final' 38 times and MLB Stats API rows reached it ZERO times, sitting at
    'preview' or 'live' forever.

    That is not cosmetic. bets/paper.settle() looks up the bet's own Stats API
    game_id and requires status='final', so no paper bet could ever settle and
    gate 2 could never fill, no matter how long it ran.

    Yesterday is the important half. Today is included because an afternoon
    game may already be over when this runs.
    """
    today = dt.date.today()
    live = active_sports(today.month)
    for i in range(days_back, -1, -1):
        day = (today - dt.timedelta(days=i)).isoformat()
        print(f"Results for {day}:")
        try:
            scores.pull_all(day)
        except requests.RequestException as e:
            print(f"  [scores] unavailable: {e}")
        if "mlb" in live:
            try:
                mlb.pull_day(day)
            except requests.RequestException as e:
                print(f"  [mlb] unavailable: {e}")


def grade():
    finals()
    # Finals just landed, so any paper bet whose game finished can now be
    # settled and, if a usable close exists, scored toward gate 2.
    try:
        from bets.paper import settle, score
        t = settle()
        if any(t.values()):
            print(f"Paper: {t['graded']} graded with CLV, "
                  f"{t['no_close'] + t['settled_no_clv']} with no usable close, "
                  f"{t['no_result']} awaiting a final")
        score()
    except Exception as e:                      # never let this kill grading
        print(f"(paper settle skipped: {e})")
    betlog.review()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if mode == "cronstatus":
        from cronstatus import report
        raise SystemExit(report())
    if mode == "paper":
        from bets.paper import run as paper_run
        paper_run()
        raise SystemExit(0)
    if mode == "backup":
        import backup as bk
        bk.take()
        bk.prune()
        raise SystemExit(0)
    {"morning": morning, "close": close, "picks": show_picks,
     "refresh": refresh, "grade": grade, "predict": predict,
     "finals": finals}[mode]()
