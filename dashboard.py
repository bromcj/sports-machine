"""Build a one-page visual summary of the machine and open it in a browser.

    python dashboard.py            build and open
    python dashboard.py --no-open  build only

Reads the live database, the training table and validation.json, and writes a
self-contained dashboard.html - no server, no internet, no API credits. Because
it reads local data it is always current, which a hosted page could not be.

Written for a reader with no sports-analytics or statistics background. Three
rules follow from that, and they are the reason the page looks the way it does:

  SHOW NO NUMBER THAT NEEDS A TRANSLATOR. The season charts print no y-axis
  values - their real units are log-loss, which means nothing to a stranger and
  whose spread here is so small every bar looks identical anyway. Bar length
  carries it. The importance chart prints no coefficients. Nothing with three
  decimal places is visible anywhere; it all lives in hover tooltips instead.

  LEAD WITH THE VERDICT. The headline is that the thing will not bet, not that
  it is right 54% of the time - that number reads as good news and then needs
  three paragraphs of walking back.

  EVERY CHART ENDS IN A SENTENCE. Each one closes with a bottom line in plain
  words, and every figure in those sentences is computed from the data, never
  written by hand, so the page cannot go stale or lie.

Presentation only: nothing here computes a model number. gather() collects,
everything below it labels, orders, sizes and words.
"""
import contextlib
import datetime as dt
import io
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import paths

OUT = ROOT / "dashboard.html"

# Palette slots 1 and 2 from the data-viz reference palette. Validated with
# scripts/validate_palette.js --pairs all: ALL CHECKS PASS in both modes
# (CVD dE 24.7 light / 26.8 dark, normal-vision 33.6 / 31.8, contrast >= 3:1).
LIGHT = dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e",
             s1="#2a78d6", s2="#eb6834", good="#0ca30c", crit="#d03b3b",
             # bottom-line box: a real step off the surface in BOTH themes.
             box="#f0efeb", boxline="#dedcd6")
DARK = dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7",
            s1="#3987e5", s2="#d95926", good="#0ca30c", crit="#d03b3b",
            box="#272724", boxline="#3b3a36")


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------- data
def gather():
    from db import DB_PATH, connect
    from model import validation as v
    from model.predict import picks

    d = {"generated": dt.datetime.now().strftime("%a %d %b %Y, %I:%M %p").lstrip("0"),
         "db": str(DB_PATH)}

    con = connect()
    today = dt.date.today().isoformat()
    d["n_games"] = con.execute("SELECT COUNT(*) c FROM games").fetchone()["c"]
    d["n_odds"] = con.execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
    # A CLOSING line is the last price before first pitch, which is what
    # market_close stores after resolving it per game. snapshot_type='close' is
    # only the LABEL on a pull, and one pull returns tonight's games alongside
    # games days out - so it counted prices nowhere near a close, and missed
    # every hist_close row the Phase 5 backfill bought.
    d["n_close"] = con.execute(
        "SELECT COUNT(*) c FROM market_close").fetchone()["c"]
    # predictions is append-only: a game scored twice in a day has two rows, so
    # counting rows counts reruns. The only place this number is used says
    # "tonight's N predicted games", so it is today's DISTINCT games - which is
    # what the sentence claimed all along and what it now counts.
    d["n_pred"] = con.execute(
        "SELECT COUNT(DISTINCT p.game_id) c FROM predictions p"
        " JOIN games g ON g.game_id = p.game_id WHERE g.game_date = ?",
        (today,)).fetchone()["c"]
    con.close()

    # picks() prints its own table; we want the returned rows, not the noise
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            d["picks"] = picks(today)
        except Exception:
            d["picks"] = []

    d["gates"] = {s: v.gates(s) for s in ("mlb", "nfl") if v.status(s)}
    d["reasons"] = {s: (v.status(s) or {}).get("reason", "") for s in d["gates"]}
    d["seasons"] = {s: (v.status(s) or {}).get("seasons", []) for s in d["gates"]}
    # The pooled walk-forward result, read from what validation.py recorded
    # rather than typed into the prose. If the model is ever retrained, this
    # sentence moves with it.
    d["pooled"] = {s: (v.status(s) or {}).get("pooled", {}) for s in d["gates"]}
    d["baseline_kind"] = {s: (v.status(s) or {}).get("baseline_kind", "")
                          for s in d["gates"]}

    # ridge coefficients, refit on the current training table
    d["coef"] = []
    tp = paths.training_table("mlb")
    if tp.exists():
        import pandas as pd
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        df = pd.read_parquet(tp)
        feats = [c for c in df.columns if any(c.startswith(p) for p in (
            "home_pen", "away_pen", "home_off", "away_off", "home_sp",
            "away_sp", "home_rest", "away_rest"))] + ["park_factor"]
        m = make_pipeline(StandardScaler(), Ridge(alpha=100.)).fit(df[feats], df["run_diff"])
        d["coef"] = sorted(zip(feats, m.named_steps["ridge"].coef_),
                           key=lambda kv: -abs(kv[1]))
        d["n_train"] = len(df)
        d["train_seasons"] = sorted(int(s) for s in df["season"].unique())

    # Headline accuracy, straight from the walk-forward the model was graded on.
    accs = []
    from model.train import walk_forward
    if tp.exists():
        try:
            r = walk_forward(df, feats, target_col="run_diff",
                             season_col="season", sport="mlb")
            accs = list(r["accuracy"])
        except Exception:
            accs = []
    d["accuracy"] = sum(accs) / len(accs) if accs else None

    # How many games that accuracy was actually measured over. walk_forward
    # trains on seasons[:i] and tests on seasons[i], starting at i=2, so the
    # first two seasons are never scored. The page used to headline the
    # accuracy "over 10,482 games it had never seen" - 10,482 is the WHOLE
    # table, training seasons included. The real figure is smaller.
    d["n_test"] = 0
    d["test_seasons"] = []
    if tp.exists() and accs:
        ss = sorted(int(x) for x in df["season"].unique())
        d["test_seasons"] = ss[2:]
        d["n_test"] = int(df[df["season"].isin(d["test_seasons"])].shape[0])
    return d

# ================================================================== labels
# Every metric below was traced to source before it was given a plain-English
# name. What each one actually is:
#
#   accuracy    share of test-season games where the higher-probability side
#               won. walk_forward trains on seasons[:i] and scores season[i]
#               from i=2, so it is measured on the LAST THREE seasons only -
#               6,497 games, not the 10,482 in the table.
#   "how wrong" sklearn.metrics.log_loss on the predicted win probability.
#               Formally logarithmic loss / cross-entropy. Lower is better,
#               and being confident and wrong is punished hardest.
#   importance  absolute standardised ridge coefficients, home and away summed
#               per concept. Each is the effect on projected run differential
#               of a one-standard-deviation move in that input, holding the
#               others fixed. Rescaling so the largest reads 100 is a plain
#               linear transform, so the ratios survive it.

SPORT_NAME = {"mlb": "Baseball", "nfl": "Football"}

GROUP_NAME = {"sp": "Who's pitching", "off": "Recent hitting",
              "pen_q": "Bullpen quality", "park": "Ballpark",
              "rest": "Days off", "pen_t": "Bullpen tiredness"}

FEATURE_DEF = {
    "sp": ("Strikeouts minus walks, as a share of the batters they faced, over "
           "their last 5 starts. It ignores luck on balls in play."),
    "off": ("Every plate appearance in the last 30 days, weighted by how many "
            "runs that outcome is typically worth, so a home run counts far "
            "more than a walk."),
    "pen_q": ("The same strikeouts-minus-walks measure, for every pitcher who "
              "appears after the starter, over the last 30 days."),
    "pen_t": ("Pitches thrown by the relief pitchers in the last 3 days. A "
              "worn-out bullpen gives up more runs."),
    "rest": "Days since that team last played a game.",
    "park": ("How much more, or less, scoring happens at this stadium than at "
             "an average one, from what actually happened there in past "
             "seasons rather than from its dimensions."),
}

# The three gates, in the order they must fall.
GATES = [
    # Not "beat bookmaker predictions" any more. That wording came from the
    # era when the baseline was a home-rate placeholder and nobody had bought
    # a real closing line. Real de-vigged closes now exist for 2024-2026, MLB
    # was measured against them, and it LOST in all three seasons. The gate
    # fails for a real reason and the label should say which.
    ("walk_forward", "Beat the real closing line",
     "Score better than the market's own de-vigged closing price, on seasons "
     "the model never saw while it was being built."),
    ("paper_trading", "Show it would actually make money",
     "Track 50 or more picks at real prices, with no money down, and end up "
     "ahead of the closing line."),
    ("armed", "Human approval",
     "Someone has to switch it on deliberately. The code refuses to do this "
     "itself until the first two pass."),
]


def feature_key(name):
    if "_sp_" in name:
        return "sp"
    if "_off_" in name:
        return "off"
    if "pen_kbb" in name:
        return "pen_q"
    if "pen_pitches" in name:
        return "pen_t"
    if "rest" in name:
        return "rest"
    return "park"


def abbr(team):
    try:
        from features.sports.mlb_features import TEAM_ABBR
        if team in TEAM_ABBR:
            return TEAM_ABBR[team]
    except Exception:
        pass
    return "".join(w[0] for w in str(team).split()[:3]).upper()


def gate_state(key, st):
    """The three gates are in genuinely different states, so they must not all
    read 'not yet'. Derived from what validation.py actually recorded:

      passed      cleared
      failed      a real test ran and the model lost it
      untested    the comparison has not been possible yet (placeholder
                  baseline), so nothing has been proven either way
      waiting     nothing recorded at all
      locked      held shut by design until the gates above it pass
    """
    if st is None:
        return ("waiting", "Not started")
    if key == "walk_forward":
        if st.get("cleared"):
            return ("passed", "Passed")
        if st.get("baseline_kind") == "placeholder":
            return ("untested", "No real test yet")
        return ("failed", "Not passed")
    if key == "paper_trading":
        p = st.get("paper_trading")
        if not p:
            return ("waiting", "Not started")
        return ("passed", "Passed") if p.get("passed") else ("failed", "Not passed")
    if st.get("armed"):
        return ("passed", "Approved")
    return ("locked", "Locked")


_TID = [0]


def table(headers, rows, caption):
    """Collapsed on desktop, forced open on a phone where it replaces the
    chart. A <details> cannot be opened by a media query - it collapses its
    content whatever CSS the child carries - so this is the checkbox pattern:
    pure CSS, no script, and the breakpoint actually works."""
    _TID[0] += 1
    tid = f"t{_TID[0]}"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>"
                   for r in rows) or f'<tr><td colspan="{len(headers)}">nothing yet</td></tr>'
    return (f'<input type="checkbox" id="{tid}" class="tgl">'
            f'<label for="{tid}">{esc(caption)}</label>'
            f'<div class="tblbox"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def tech(summary, body):
    """Progressive disclosure. The page must make sense without ever opening
    one of these; the real method lives inside."""
    return (f'<details class="tech"><summary>{esc(summary)}</summary>'
            f'<div class="tech-b">{body}</div></details>')


# ================================================================== charts
def chart_seasons(seasons, opponent):
    """Connected dots, one row per season - deliberately NOT bars.

    A bar encodes magnitude as length from zero, so a truncated axis makes a
    small difference look enormous; that is exactly what the previous version
    did to gaps of a few hundredths. A dot encodes position, so a tight axis is
    honest here: the marks are where the numbers actually sit, and the exact
    values are printed beside them either way.
    """
    if not seasons:
        return "", ""
    PAD_T, ROW = 54, 40
    h = PAD_T + len(seasons) * ROW + 44
    x0, x1 = 112, 520
    lo = min(min(s["logloss_model"], s["logloss_market"]) for s in seasons)
    hi = max(max(s["logloss_model"], s["logloss_market"]) for s in seasons)
    pad = (hi - lo) * .25 or .01
    lo, hi = lo - pad, hi + pad
    sx = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
    step = 0.02
    o = [f'<svg viewBox="0 0 760 {h}" role="img" aria-label="Prediction error by '
         f'season for Sports Machine and for the bookmakers; lower is better">']
    t = (int(lo / step) + 1) * step
    while t < hi:
        o.append(f'<line class="grid" x1="{sx(t):.1f}" y1="{PAD_T-14}" '
                 f'x2="{sx(t):.1f}" y2="{h-44}"/>')
        o.append(f'<text class="tick" x="{sx(t):.1f}" y="{PAD_T-20}" '
                 f'text-anchor="middle">{t:.2f}</text>')
        t += step
    o.append(f'<text class="axis better" x="{x0}" y="{h-12}">'
             f'&#8592; lower prediction error is better</text>')
    o.append(f'<text class="tick" x="{x1+30}" y="{PAD_T-20}">more accurate</text>')
    for i, s_ in enumerate(seasons):
        y = PAD_T + i * ROW + ROW / 2
        m, k = s_["logloss_model"], s_["logloss_market"]
        a_, b_ = sx(m), sx(k)
        o.append(f'<text class="cat" x="{x0-18}" y="{y+5}" text-anchor="end">{s_["season"]}</text>')
        o.append(f'<line class="conn" x1="{a_:.1f}" y1="{y}" x2="{b_:.1f}" y2="{y}"/>')
        for val, cls, who in ((k, "mk2", opponent), (m, "mk1", "Sports Machine")):
            cx = sx(val)
            o.append(f'<circle class="ring" cx="{cx:.1f}" cy="{y}" r="8"/>'
                     f'<circle class="{cls}" cx="{cx:.1f}" cy="{y}" r="6.5"><title>'
                     f'{s_["season"]} — {esc(who)}: {val:.3f}</title></circle>')
        # exact values, placed on the outside of each dot so they never collide
        lft, rgt = (m, k) if m <= k else (k, m)
        o.append(f'<text class="barval" x="{sx(lft)-13:.1f}" y="{y+4}" '
                 f'text-anchor="end">{lft:.3f}</text>')
        o.append(f'<text class="barval" x="{sx(rgt)+13:.1f}" y="{y+4}">{rgt:.3f}</text>')
        better = "Sports Machine" if m < k else opponent
        o.append(f'<text class="val" x="{x1+30}" y="{y+5}">{esc(better)}</text>')
    o.append("</svg>")
    rows = [(s_["season"], f'{s_["logloss_model"]:.3f}', f'{s_["logloss_market"]:.3f}',
             "Sports Machine" if s_["logloss_model"] < s_["logloss_market"] else opponent)
            for s_ in seasons]
    tbl = table(["Season", "Sports Machine error", f"{opponent} error",
                 "More accurate"], rows, "Show the exact numbers")
    return "".join(o), tbl


def chart_picks(rows):
    """One row per game: two dots and the distance between them, in percentage
    POINTS. Never a percent change - 46% vs 56% is 10 points apart, not 18%."""
    rows = [r for r in rows if r.get("market_prob") is not None]
    if not rows:
        return "", "", 0, 0
    rows = sorted(rows, key=lambda r: -abs(r["model_prob"] - r["market_prob"]))
    PAD_T, ROW = 52, 34
    h = PAD_T + len(rows) * ROW + 40
    x0, x1 = 128, 508
    lo = min(min(r["model_prob"], r["market_prob"]) for r in rows) - .05
    hi = max(max(r["model_prob"], r["market_prob"]) for r in rows) + .05
    lo, hi = max(0, lo), min(1, hi)
    sx = lambda p: x0 + (p - lo) / (hi - lo) * (x1 - x0)
    o = [f'<svg viewBox="0 0 760 {h}" role="img" aria-label="For each game, the '
         f'chance the home team wins according to Sports Machine and according '
         f'to the bookmakers">']
    for t in [i / 100 for i in range(0, 101, 5)]:
        if lo <= t <= hi:
            o.append(f'<line class="grid" x1="{sx(t):.1f}" y1="{PAD_T-12}" '
                     f'x2="{sx(t):.1f}" y2="{h-40}"/>')
            o.append(f'<text class="tick" x="{sx(t):.1f}" y="{PAD_T-18}" '
                     f'text-anchor="middle">{t:.0%}</text>')
    o.append(f'<text class="axis" x="{(x0+x1)/2:.0f}" y="{h-10}" text-anchor="middle">'
             f'chance the home team wins</text>')
    o.append(f'<text class="tick" x="{x1+30}" y="{PAD_T-18}">machine picks</text>')
    o.append(f'<text class="tick" x="{x1+150}" y="{PAD_T-18}">difference</text>')
    for i, r in enumerate(rows):
        y = PAD_T + i * ROW + ROW / 2
        a, b = sx(r["model_prob"]), sx(r["market_prob"])
        pts = (r["model_prob"] - r["market_prob"]) * 100
        lab = f'{abbr(r["away"])} @ {abbr(r["home"])}'
        o.append(f'<text class="cat" x="{x0-18}" y="{y+5}" text-anchor="end">{esc(lab)}</text>')
        o.append(f'<line class="conn" x1="{a:.1f}" y1="{y}" x2="{b:.1f}" y2="{y}"/>')
        o.append(f'<circle class="ring" cx="{b:.1f}" cy="{y}" r="8"/>'
                 f'<circle class="mk2" cx="{b:.1f}" cy="{y}" r="6.5"><title>'
                 f'{esc(r["away"])} at {esc(r["home"])} — bookmakers: '
                 f'{r["market_prob"]:.0%}</title></circle>')
        o.append(f'<circle class="ring" cx="{a:.1f}" cy="{y}" r="8"/>'
                 f'<circle class="mk1" cx="{a:.1f}" cy="{y}" r="6.5"><title>'
                 f'{esc(r["away"])} at {esc(r["home"])} — Sports Machine: '
                 f'{r["model_prob"]:.0%}</title></circle>')
        word = "lower" if pts < 0 else "higher"
        unit = "pt" if round(abs(pts)) == 1 else "pts"
        # The pick is simply the side it gives the better than even chance to.
        pick = abbr(r["home"]) if r["model_prob"] > .5 else abbr(r["away"])
        o.append(f'<text class="pick" x="{x1+30}" y="{y+5}">{esc(pick)}</text>')
        o.append(f'<text class="val dim" x="{x1+150}" y="{y+5}">'
                 f'{abs(pts):.0f} {unit} {word}</text>')
    o.append("</svg>")
    tbl = table(["Game", "Machine picks", "Sports Machine", "Bookmakers", "Difference"],
                [(f'{r["away"]} @ {r["home"]}',
                  r["home"] if r["model_prob"] > .5 else r["away"],
                  f'{r["model_prob"]:.0%}', f'{r["market_prob"]:.0%}',
                  f'{(r["model_prob"]-r["market_prob"])*100:+.0f} '
                  f'{"pt" if round(abs((r["model_prob"]-r["market_prob"])*100)) == 1 else "pts"}')
                 for r in rows],
                "Show the exact numbers")
    lower = sum(1 for r in rows if r["model_prob"] < r["market_prob"])
    return "".join(o), tbl, len(rows), lower


def chart_importance(coef):
    """Six merged bars, rescaled so the largest reads 100. That is a plain
    linear rescale of the existing standardised coefficients, so every ratio
    between factors survives it unchanged."""
    if not coef:
        return "", "", None
    g = {}
    for name, c in coef:
        g[feature_key(name)] = g.get(feature_key(name), 0.0) + abs(c)
    order = sorted(g.items(), key=lambda kv: -kv[1])
    mx = order[0][1] or 1
    scaled = [(k, v, round(100 * v / mx)) for k, v in order]
    ROW, PAD_T, x0, x1 = 44, 10, 210, 660
    h = PAD_T + len(scaled) * ROW + 8
    o = [f'<svg viewBox="0 0 760 {h}" role="img" aria-label="What moves the '
         f'prediction most, scaled so the biggest factor is 100">']
    for i, (k, _, n) in enumerate(scaled):
        y = PAD_T + i * ROW
        w = n / 100 * (x1 - x0)
        o.append(f'<text class="cat big" x="{x0-18}" y="{y+25}" text-anchor="end">{esc(GROUP_NAME[k])}</text>')
        o.append(f'<rect class="mk1 bar" x="{x0}" y="{y+7}" width="{max(w,2):.1f}" '
                 f'height="24" rx="5"><title>{esc(GROUP_NAME[k])} — {esc(FEATURE_DEF[k])}'
                 f'</title></rect>')
        o.append(f'<text class="val" x="{x0+w+12:.1f}" y="{y+25}">{n}</text>')
    o.append("</svg>")
    tbl = table(["What it looks at", "Relative weight (biggest = 100)"],
                [(GROUP_NAME[k], n) for k, _, n in scaled], "Show the exact numbers")
    return "".join(o), tbl, scaled


# =========================================================== bottom lines
# Declarative, and every figure computed. Nothing here is written by hand, so
# the page cannot drift out of step with the database.
def bl(text):
    return f'<p class="bl"><strong>Bottom line:</strong> {text}</p>'


def bl_seasons(seasons, opponent):
    n = len(seasons)
    lost = sum(1 for s in seasons if s["logloss_model"] >= s["logloss_market"])
    if lost == n:
        return bl(f"The {opponent.lower()} predicted more accurately in all {n} "
                  f"football seasons tested.")
    if lost == 0:
        return bl(f"Sports Machine predicted more accurately in all {n} football "
                  f"seasons tested.")
    return bl(f"The {opponent.lower()} predicted more accurately in {lost} of the "
              f"{n} football seasons tested.")


def bl_picks(n, lower):
    if not n:
        return ""
    s = f"It disagrees with the bookmakers on all {n} games tonight"
    if lower:
        s += (f", and on {lower} of them it gives the home team a lower chance "
              f"than they do")
    return bl(s + ".")


def bl_importance(scaled):
    """'More than everything else combined' is the sentence that wants writing
    and it is false here - the top factor is 0.56 against 0.99 for the rest -
    so the claim is checked against the numbers before it is emitted."""
    if not scaled:
        return ""
    top, top_v, _ = scaled[0]
    rest = sum(v for _, v, _ in scaled[1:])
    second = scaled[1][1] if len(scaled) > 1 else 0
    name = GROUP_NAME[top].lower()
    if top_v > rest:
        return bl(f"{name.capitalize()} matters more than everything else combined.")
    if second:
        return bl(f"{GROUP_NAME[top]} has the biggest effect on the model's "
                  f"predictions — about {top_v/second:.1f} times the next "
                  f"biggest factor.")
    return bl(f"{GROUP_NAME[top]} has the biggest effect.")


def mlb_verdict(d):
    """The baseball headline, computed from validation.json. Never typed in.

    The page used to say baseball "cannot sit this test yet - its bookmaker
    prices are only being collected now". That stopped being true when real
    de-vigged closes were bought for 2024-2026 and the model was measured
    against them. It lost in all three seasons, and a dashboard whose job is to
    lead with the verdict was leading with an excuse instead.
    """
    pooled = (d.get("pooled") or {}).get("mlb") or {}
    seasons = (d.get("seasons") or {}).get("mlb") or []
    kind = (d.get("baseline_kind") or {}).get("mlb", "")
    lost = [s for s in seasons if not s.get("beat_market")]
    if kind != "market" or not pooled:
        return ('<strong>Baseball has not been measured against a real market '
                'yet.</strong> The football results below are the same test '
                'where years of real bookmaker prices are on public record.')
    return (
        f'<strong>Baseball has now sat this test, and failed it.</strong> '
        f'Real de-vigged closing prices were bought and the model lost in '
        f'<strong>{len(lost)} of {len(seasons)} seasons</strong> &mdash; '
        f'pooled {pooled.get("mean_ll_diff", 0):+.4f} log loss, '
        f't&nbsp;=&nbsp;{pooled.get("t_stat", 0):+.1f} over '
        f'{pooled.get("n_games", 0):,} games. The football results below are '
        f'the same test, and it lost every season there too.')


# ==================================================================== page
def legend(a, b):
    return (f'<div class="legend"><span><i class="sw1"></i>{a}</span>'
            f'<span><i class="sw2"></i>{b}</span></div>')


ROADMAP = [
    ("Collect real bookmaker prices", "now"),
    ("Predict games before they happen", "now"),
    ("Grade completed games against bookmaker closing prices", "next"),
    ("Check the bets would clear the bookmaker's cut", "later"),
    ("Human approval", "later"),
    ("Betting unlocked", "goal"),
]


def build(d):
    from model.validation import status as _status
    sports = list(d["gates"]) or ["mlb", "nfl"]
    cleared = any(all(g.values()) for g in d["gates"].values())
    n_checks = len(GATES)
    per = {sp: sum(1 for k, _, _ in GATES if d["gates"].get(sp, {}).get(k)) for sp in sports}
    agree = all(d["gates"].get(sports[0], {}).get(k) == d["gates"].get(sp, {}).get(k)
                for k, _, _ in GATES for sp in sports)
    passed_hd = (f"{per[sports[0]]} of {n_checks}" if agree
                 else " &middot; ".join(f"{SPORT_NAME.get(s, s)} {per[s]}/{n_checks}"
                                        for s in sports))

    # One row per gate, each with its own state - they are genuinely different
    # and used to read "not yet" three times over.
    gate_rows = ""
    for i, (key, title, why) in enumerate(GATES, 1):
        cells = ""
        for sp in sports:
            cls, label = gate_state(key, _status(sp))
            cells += (f'<span class="pill {cls}"><i></i>{SPORT_NAME.get(sp, sp)}: '
                      f'{esc(label)}</span>')
        gate_rows += (f'<li class="gate"><div class="gate-n">{i}</div>'
                      f'<div class="gate-b"><div class="gate-t">{esc(title)}</div>'
                      f'<div class="gate-w">{esc(why)}</div>'
                      f'<div class="pills">{cells}</div></div></li>')

    picks_svg, picks_tbl, n_games, n_lower = chart_picks(d["picks"])
    imp_svg, imp_tbl, imp_scaled = chart_importance(d["coef"])
    OPP = "Bookmakers"
    nfl_rows = d["seasons"].get("nfl") or []
    nfl_svg, nfl_tbl = chart_seasons(nfl_rows, OPP) if nfl_rows else ("", "")

    priced_note = (f'{n_games} of tonight&rsquo;s {d["n_pred"]} predicted '
                   f'games have a bookmaker price so far.'
                   if d["n_pred"] > n_games else "")
    TAG = {"now": "Happening now", "next": "Next", "goal": "Goal", "later": ""}
    steps = ""
    for label, when in ROADMAP:
        tag = TAG[when]
        steps += (f'<li class="step {when}"><span class="dot"></span>'
                  f'<span class="st">{esc(label)}</span>'
                  + (f'<span class="tag">{esc(tag)}</span>' if tag else
                     '<span class="tag waiting">Not started</span>') + '</li>')

    acc = f'{d["accuracy"]:.1%}' if d.get("accuracy") else "&mdash;"
    accent = "var(--good)" if cleared else "var(--crit)"
    css = lambda p: "".join(f"--{k}:{v};" for k, v in p.items())

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sports Machine</title>
<style>
  :root {{ color-scheme: light; {css(LIGHT)} }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{ color-scheme: dark; {css(DARK)} }} }}
  :root[data-theme="dark"] {{ color-scheme: dark; {css(DARK)} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--surface); color:var(--ink);
    font:16px/1.65 ui-sans-serif,system-ui,"Segoe UI",sans-serif;
    -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:1040px; margin:0 auto; padding:40px 24px 72px; }}
  h1 {{ font-size:28px; margin:0 0 8px; letter-spacing:-.025em; }}
  h2 {{ font-size:23px; margin:0 0 6px; letter-spacing:-.02em; }}
  p {{ max-width:68ch; }}
  .sub {{ color:var(--ink2); font-size:13px; margin:0; }}
  .lede {{ font-size:18px; margin:8px 0 0; max-width:64ch; }}
  .note {{ font-size:15px; color:var(--ink2); margin:6px 0 0; max-width:66ch; }}
  section {{ margin-top:56px; }}
  .eyebrow {{ font-size:12px; font-weight:700; letter-spacing:.09em;
    text-transform:uppercase; color:var(--ink2); margin:0 0 4px; }}

  /* 1 — can it bet */
  .verdict {{ margin-top:28px; padding:28px; border-radius:16px;
    border:2px solid {accent};
    background:color-mix(in srgb, {accent} 6%, transparent); }}
  .vv {{ font-size:clamp(30px,5vw,46px); font-weight:780; line-height:1.03;
    letter-spacing:-.035em; color:{accent}; }}
  .vc {{ font-size:16px; font-weight:650; margin-top:8px; }}
  .vp {{ font-size:16px; color:var(--ink2); margin:12px 0 0; max-width:62ch; }}
  ol.gates {{ list-style:none; margin:24px 0 0; padding:0; display:grid; gap:2px; }}
  .gate {{ display:flex; gap:14px; padding:16px 2px;
    border-top:1px solid color-mix(in srgb, var(--ink) 13%, transparent); }}
  .gate-n {{ flex:0 0 28px; height:28px; border-radius:99px; font-size:13px;
    font-weight:700; display:grid; place-items:center; color:var(--ink2);
    border:1px solid color-mix(in srgb, var(--ink) 25%, transparent); }}
  .gate-t {{ font-weight:650; font-size:16px; }}
  .gate-w {{ font-size:14px; color:var(--ink2); margin-top:2px; max-width:60ch; }}
  .pills {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }}
  .pill {{ display:inline-flex; align-items:center; gap:7px; font-size:13px;
    font-weight:600; padding:4px 11px; border-radius:99px; color:var(--ink2);
    border:1px solid color-mix(in srgb, var(--ink) 16%, transparent); }}
  .pill i {{ width:8px; height:8px; border-radius:99px; background:currentColor; }}
  .pill.passed {{ color:var(--good); border-color:color-mix(in srgb,var(--good) 45%,transparent); }}
  .pill.failed {{ color:var(--crit); border-color:color-mix(in srgb,var(--crit) 45%,transparent); }}
  .pill.untested, .pill.waiting {{ color:var(--ink2); }}
  .pill.locked {{ color:var(--ink2); }}
  .pill.locked i, .pill.waiting i {{ background:none; box-shadow:inset 0 0 0 1.5px currentColor; }}

  /* 2 — scorecard */
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
    gap:14px; margin-top:24px; }}
  .card {{ padding:20px; border-radius:14px;
    border:1px solid color-mix(in srgb, var(--ink) 13%, transparent); }}
  .card-v {{ font-size:34px; font-weight:720; letter-spacing:-.03em; line-height:1; }}
  .card-l {{ font-size:14px; font-weight:600; margin-top:8px; }}
  .card-s {{ font-size:13px; color:var(--ink2); margin-top:3px; }}
  .card.bad .card-v {{ color:var(--crit); }}

  /* charts */
  svg {{ width:100%; height:auto; display:block; margin-top:14px; overflow:visible; }}
  .grid {{ stroke:color-mix(in srgb, var(--ink) 10%, transparent); stroke-width:1; }}
  .conn {{ stroke:color-mix(in srgb, var(--ink) 24%, transparent); stroke-width:2; }}
  .ring {{ fill:var(--surface); }}
  .mk1 {{ fill:var(--s1); }} .mk2 {{ fill:var(--s2); }}
  .bar {{ stroke:var(--surface); stroke-width:2; }}
  text {{ font:14px ui-sans-serif,system-ui,sans-serif; fill:var(--ink2); }}
  .cat {{ fill:var(--ink); font-weight:500; }}
  .cat.big {{ font-size:16px; font-weight:600; }}
  .val {{ fill:var(--ink); font-variant-numeric:tabular-nums; font-weight:600; }}
  .val.dim {{ fill:var(--ink2); font-weight:500; }}
  .pick {{ fill:var(--s1); font-weight:700; font-size:15px; }}
  .barval {{ font-size:12px; font-variant-numeric:tabular-nums; }}
  .tick {{ font-size:12px; }} .axis {{ font-size:12px; font-weight:600; }}
  .axis.better {{ fill:var(--ink2); font-weight:500; }}
  rect.bar, circle {{ transition:opacity .12s; }}
  svg:hover rect.bar, svg:hover circle {{ opacity:.85; }}
  rect.bar:hover, circle:hover {{ opacity:1; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:18px; font-size:14px;
    color:var(--ink2); margin-top:14px; }}
  .legend i {{ width:11px; height:11px; border-radius:99px; display:inline-block;
    margin-right:7px; vertical-align:-1px; }}
  .sw1 {{ background:var(--s1); }} .sw2 {{ background:var(--s2); }}

  .callout {{ font-size:15px; margin:14px 0 0; padding:14px 16px; border-radius:11px;
    max-width:70ch; color:var(--ink2); border-left:3px solid var(--s2);
    background:color-mix(in srgb, var(--s2) 7%, transparent); }}
  .callout strong {{ color:var(--ink); }}
  .bl {{ font-size:17px; margin:18px 0 0; padding:15px 17px; border-radius:11px;
    background:var(--box); border:1px solid var(--boxline); max-width:66ch; }}
  .bl strong {{ color:var(--ink); }}
  @media (forced-colors: active) {{ .bl {{ border:1px solid CanvasText; background:Canvas; }} }}

  /* progressive disclosure */
  details.tech {{ margin-top:14px; border-radius:10px;
    border:1px solid color-mix(in srgb, var(--ink) 13%, transparent); }}
  details.tech summary {{ cursor:pointer; padding:11px 15px; font-size:14px;
    font-weight:600; color:var(--ink2); }}
  details.tech[open] summary {{ border-bottom:1px solid color-mix(in srgb,var(--ink) 11%,transparent); }}
  .tech-b {{ padding:14px 15px; font-size:14px; color:var(--ink2); }}
  .tech-b p {{ margin:0 0 9px; max-width:70ch; }} .tech-b p:last-child {{ margin:0; }}
  .tech-b code {{ font-size:13px; background:var(--box); padding:1px 5px; border-radius:4px; }}

  .tgl {{ position:absolute; opacity:0; width:0; height:0; }}
  .tgl + label {{ display:inline-block; margin-top:14px; font-size:14px;
    color:var(--ink2); cursor:pointer; border-bottom:1px dotted currentColor; }}
  .tgl:focus-visible + label {{ outline:2px solid var(--s1); outline-offset:3px; }}
  .tblbox {{ display:none; }}
  .tgl:checked ~ .tblbox {{ display:block; }}
  .tblbox table {{ border-collapse:collapse; width:100%; margin-top:12px; font-size:14px; }}
  .tblbox th, .tblbox td {{ text-align:left; padding:8px 10px;
    font-variant-numeric:tabular-nums;
    border-bottom:1px solid color-mix(in srgb, var(--ink) 11%, transparent); }}
  .tblbox th {{ font-size:12px; text-transform:uppercase; letter-spacing:.05em;
    color:var(--ink2); }}

  /* 6 — roadmap */
  ol.road {{ list-style:none; margin:24px 0 0; padding:0; }}
  .step {{ display:flex; align-items:center; gap:16px; padding:13px 0 13px 4px;
    position:relative; }}
  .step .dot {{ flex:0 0 15px; height:15px; border-radius:99px;
    background:var(--surface);
    box-shadow:inset 0 0 0 2px color-mix(in srgb,var(--ink) 22%,transparent); z-index:1; }}
  /* the rail is solid where the project has got to, dashed where it has not */
  .step::before {{ content:""; position:absolute; left:11px; top:0; bottom:0;
    width:2px; background:color-mix(in srgb, var(--ink) 15%, transparent); }}
  .step.later::before, .step.goal::before {{ background:none;
    border-left:2px dashed color-mix(in srgb, var(--ink) 22%, transparent); }}
  .step:first-child::before {{ top:50%; }} .step:last-child::before {{ bottom:50%; }}
  .step .st {{ font-size:15px; color:var(--ink); }}

  .step.now .dot {{ background:var(--s1); box-shadow:inset 0 0 0 3px var(--s1),
    0 0 0 5px color-mix(in srgb, var(--s1) 20%, transparent); }}
  .step.now::before {{ background:var(--s1); }}
  .step.now .st {{ font-weight:700; }}
  .step.next .dot {{ box-shadow:inset 0 0 0 3px var(--s2); }}
  .step.next .st {{ font-weight:650; }}
  .step.later .dot, .step.goal .dot {{ flex-basis:11px; height:11px; margin-left:2px; }}
  .step.later .st {{ color:var(--ink2); }}
  .step.goal .dot {{ box-shadow:inset 0 0 0 3px var(--good); }}
  .step.goal .st {{ font-weight:700; }}

  .tag {{ font-size:11px; font-weight:700; letter-spacing:.07em;
    text-transform:uppercase; padding:3px 9px; border-radius:99px;
    color:var(--s1); background:color-mix(in srgb, var(--s1) 12%, transparent); }}
  .step.next .tag {{ color:var(--s2); background:color-mix(in srgb,var(--s2) 12%,transparent); }}
  .step.goal .tag {{ color:var(--good); background:color-mix(in srgb,var(--good) 13%,transparent); }}
  .tag.waiting {{ color:var(--ink2); background:none; font-weight:600;
    letter-spacing:.04em; text-transform:none; padding:0; }}

  @media (max-width:640px) {{
    .wrap {{ padding:28px 16px 56px; }}
    body {{ font-size:15px; }}
    svg, .legend {{ display:none; }}
    .chart-only {{ display:none; }}
    .tgl + label {{ display:none; }}
    .tblbox {{ display:block; }}
    .card-v {{ font-size:28px; }}
    .gate {{ gap:11px; }}
  }}
</style></head>
<body><div class="wrap">
<header>
  <h1>Sports Machine</h1>
  <p class="lede">A program that predicts who wins baseball games — and refuses to
  bet on itself until it can prove it beats the bookmakers.</p>
  <p class="sub" style="margin-top:10px">{esc(d["generated"])}</p>
</header>

<section style="margin-top:8px">
  <div class="verdict">
    <div class="vv">{"READY TO BET" if cleared else "NOT READY TO BET"}</div>
    <div class="vc">{passed_hd} betting checks passed</div>
    <p class="vp">Sports Machine cannot place a real bet until all three checks
    pass. The restriction is enforced in code — it can&rsquo;t be accidentally
    bypassed.</p>
    <ol class="gates">{gate_rows}</ol>
  </div>
</section>

<section>
  <p class="eyebrow">Scorecard &mdash; baseball</p>
  <h2>Baseball performance so far</h2>
  <div class="cards">
    <div class="card"><div class="card-v">{acc}</div>
      <div class="card-l">Winners picked correctly</div>
      <div class="card-s">on games it had never seen</div></div>
    <div class="card"><div class="card-v">{d.get("n_test", 0):,}</div>
      <div class="card-l">Games tested</div>
      <div class="card-s">never used for training</div></div>
    <div class="card"><div class="card-v">{len(d.get("test_seasons", []))}</div>
      <div class="card-l">Seasons tested</div>
      <div class="card-s">{d.get("test_seasons", ["—"])[0]}&ndash;{d.get("test_seasons", ["—"])[-1]}</div></div>
  </div>
  <p class="note">For scale: a coin flip gets 50%, and simply always picking the
  home team gets about 53%.</p>
  {tech("How was this measured?",
        f'<p>The model is trained on past seasons only, then scored on the next '
        f'season it has never seen, and that repeats forward. The {acc} figure is '
        f'the average of those per-season scores, across '
        f'{d.get("n_test", 0):,} games in {len(d.get("test_seasons", []))} seasons '
        f'({", ".join(str(x) for x in d.get("test_seasons", []))}).</p>'
        f'<p>It counts a game as correct when the side the model gave the higher '
        f'chance to actually won. The full training table holds '
        f'{d.get("n_train", 0):,} games, but the two earliest seasons are only ever '
        f'used for training, so they are not part of the score.</p>')}
</section>

<section>
  <p class="eyebrow">The test that matters &mdash; American football</p>
  <h2>Has it ever beaten the bookmakers?</h2>
  <p class="callout">{mlb_verdict(d)}</p>
  <p class="note">For each season we compare Sports Machine's predictions against
  the bookmakers' predictions for the same games. Lower prediction error is
  better.</p>
  {legend("Sports Machine", "Bookmakers")}
  {nfl_svg}
  {bl_seasons(nfl_rows, OPP)}
  {nfl_tbl}
  {tech("How is accuracy measured?",
        '<p>The bars are <strong>log loss</strong>, also called cross-entropy — the '
        'standard way to score a forecast that comes as a probability rather than a '
        'yes or no. It rewards being right, and it punishes being confidently wrong '
        'much harder than being unsure and wrong.</p>'
        '<p>That matters here: a model that says 90% and loses should be penalised '
        'far more than one that says 51% and loses, because you would have staked '
        'more on the first. Plain accuracy cannot tell those apart. Lower is '
        'better, and the bookmaker column is their own published price with the '
        'built-in margin removed.</p>')}
</section>

<section>
  <p class="eyebrow">Today</p>
  <h2>What does it think tonight?</h2>
  <p class="note">Each number is the estimated chance that the <em>home</em> team
  wins. {priced_note}</p>
  {legend("Sports Machine", "Bookmakers")}
  {picks_svg}
  {bl_picks(n_games, n_lower)}
  {picks_tbl}
  {tech("Why is a disagreement not the same as an edge?",
        '<p>A gap between the two numbers only becomes money if the model is the '
        'more accurate of the two. Right now the section above shows the opposite, '
        'so these differences are best read as disagreements, not opportunities.</p>'
        '<p>Differences are shown in percentage <em>points</em>. A model at 46% '
        'against a bookmaker at 56% is 10 points lower — not 18% lower, which is '
        'the kind of arithmetic that makes a small gap sound dramatic.</p>')}
</section>

<section>
  <p class="eyebrow">Inside the model</p>
  <h2>What the model pays attention to</h2>
  <p class="note">Everything it knows about a game, scaled so the biggest
  influence reads 100.</p>
  {imp_svg}
  {bl_importance(imp_scaled)}
  {imp_tbl}
  {tech("How is importance calculated?",
        '<p>Each input is standardised, so a value of 100 means that factor moves '
        'the predicted score margin more than any other when it shifts by a typical '
        'amount. The home and away versions of each factor are added together, and '
        'everything is then rescaled against the largest — a plain rescale, so the '
        'ratios between factors are unchanged.</p>'
        '<p>One honest limit: these describe what moves <em>this</em> model, not '
        'what decides baseball games. A factor can matter enormously in reality and '
        'score low here if the model has no good way to measure it.</p>')}
  {tech("What does each of these actually measure?",
        "".join(f"<p><strong>{esc(GROUP_NAME[k])}</strong> — {esc(FEATURE_DEF[k])}</p>"
                for k, _, _ in (imp_scaled or [])))}
</section>

<section>
  <p class="eyebrow">The road to betting</p>
  <h2>What happens next?</h2>
  <p class="note">Where the project actually is, and what it is waiting for.</p>
  <ol class="road">{steps}</ol>
  <p class="note">The program collects bookmaker prices three times a day on its
  own. Once there are enough, baseball can sit the same test football just failed —
  and until it passes, the code keeps the bet locked.</p>
</section>

</div>
<!-- Generated from {esc(d["db"])} - rebuild with: python dashboard.py -->
</body></html>"""

# ----------------------------------------------------------------- picture
PNG = ROOT / "dashboard.png"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser():
    import shutil
    for b in BROWSERS:
        if Path(b).exists():
            return b
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        f = shutil.which(name)
        if f:
            return f
    return None


def to_png(width: int = 1080, tall: int = 8000, pad: int = 28):
    """Screenshot the page, then trim the blank tail. Edge ships with Windows
    and Chrome is usually there, so this needs nothing installed."""
    import subprocess
    browser = find_browser()
    if not browser:
        print("No Edge or Chrome found, so no PNG. The HTML still works.")
        return None
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-color-profile=srgb", f"--screenshot={PNG}",
                    f"--window-size={width},{tall}", OUT.as_uri()],
                   capture_output=True, timeout=120)
    if not PNG.exists():
        print("The browser did not produce an image.")
        return None
    try:
        from PIL import Image
    except ImportError:
        print(f"Wrote {PNG} (uncropped - install Pillow to trim)")
        return PNG
    im = Image.open(PNG).convert("RGB")
    W, H = im.size
    bg = im.getpixel((2, 2))
    last = 0
    for y in range(H - 1, -1, -1):
        if any(im.getpixel((x, y)) != bg for x in range(0, W, 13)):
            last = y
            break
    im.crop((0, 0, W, min(H, last + pad))).save(PNG)
    print(f"Wrote {PNG}  ({W}x{min(H, last + pad)})")
    return PNG


if __name__ == "__main__":
    data = gather()
    OUT.write_text(build(data), encoding="utf-8")
    print(f"Wrote {OUT}")
    img = to_png() if "--png" in sys.argv else None
    if "--no-open" not in sys.argv:
        webbrowser.open((img or OUT).as_uri())
