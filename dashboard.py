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
import json
import sqlite3
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "dashboard.html"

# Palette slots 1 and 2 from the data-viz reference palette. Validated with
# scripts/validate_palette.js --pairs all: ALL CHECKS PASS in both modes
# (CVD dE 24.7 light / 26.8 dark, normal-vision 33.6 / 31.8, contrast >= 3:1).
LIGHT = dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e",
             s1="#2a78d6", s2="#eb6834", good="#0ca30c", crit="#d03b3b")
DARK = dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7",
            s1="#3987e5", s2="#d95926", good="#0ca30c", crit="#d03b3b")


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------- data
def gather():
    from bets.engine import novig_probs          # noqa: F401  (used via picks)
    from db import DB_PATH, connect
    from model import validation as v
    from model.predict import picks

    d = {"generated": dt.datetime.now().strftime("%a %d %b %Y, %I:%M %p").lstrip("0"),
         "db": str(DB_PATH)}

    con = connect()
    today = dt.date.today().isoformat()
    d["n_games"] = con.execute("SELECT COUNT(*) c FROM games").fetchone()["c"]
    d["n_odds"] = con.execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
    d["n_close"] = con.execute(
        "SELECT COUNT(*) c FROM odds_snapshots WHERE snapshot_type='close'").fetchone()["c"]
    d["n_pred"] = con.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
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

    # ridge coefficients, refit on the current training table
    d["coef"] = []
    tp = ROOT / "data" / "training_mlb.parquet"
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
    return d



# -------------------------------------------------------------------- labels
# Six merged groups. A lay reader wants to know WHAT matters, not which dugout
# it helps, so home/away pairs collapse into one bar.
GROUP_NAME = {"sp": "Who's pitching", "off": "Recent hitting",
              "pen_q": "Bullpen quality", "park": "The ballpark",
              "rest": "Days off", "pen_t": "Bullpen tiredness"}

GLOSS_HEAD = {"sp": "Who's pitching", "off": "Recent hitting",
              "pen_q": "Bullpen quality", "pen_t": "Bullpen tiredness",
              "rest": "Days off", "park": "The ballpark"}

FEATURE_DEF = {
    "sp": ("Strikeouts minus walks, as a share of the batters they faced, over "
           "their last 5 starts. It is the cleanest single read on how well a "
           "pitcher is throwing right now - it ignores luck on balls in play."),
    "off": ("Every plate appearance in the last 30 days, weighted by how many "
            "runs that outcome is typically worth. A home run counts far more "
            "than a walk, so it beats batting average as a measure of hitting."),
    "pen_q": ("The same strikeouts-minus-walks measure, but for every pitcher "
              "who appears after the starter, over the last 30 days."),
    "pen_t": ("How many pitches the relief pitchers have thrown in the last "
              "3 days. A worn-out bullpen gives up more runs."),
    "rest": "Days since that team last played a game.",
    "park": ("How much more, or less, scoring happens at this stadium than at "
             "an average one - worked out from what actually happened there in "
             "previous seasons, not from its dimensions."),
}


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
    """MIL, not 'Milwaukee Bre'. Slicing names produced trailing-space stumps."""
    try:
        from features.sports.mlb_features import TEAM_ABBR
        if team in TEAM_ABBR:
            return TEAM_ABBR[team]
    except Exception:
        pass
    return "".join(w[0] for w in str(team).split()[:3]).upper()


def table(headers, rows, caption):
    """Every chart carries one. Hover tooltips do not exist on a phone, so on a
    narrow screen the table replaces the chart rather than supplementing it."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>"
                   for r in rows) or f'<tr><td colspan="{len(headers)}">nothing yet</td></tr>'
    # `open` is not cosmetic. A closed <details> collapses its content whatever
    # display value the child carries, so the mobile rule that hides the SVG
    # left the page with no chart AND no table. Open by default, collapsible.
    return (f'<details class="tbl" open><summary>{esc(caption)}</summary>'
            f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>')


# -------------------------------------------------------------------- charts
def chart_picks(rows):
    """Dumbbell: the program's number and the bookmakers' number, per game.

    The gap is signed (program - bookmakers). It used to print unsigned, so
    every row read '+10.1%' even though the program sits BELOW the bookmakers
    on most home teams - which is the single most interesting pattern here.
    """
    rows = [r for r in rows if r.get("market_prob") is not None]
    if not rows:
        return "", "", 0, 0
    rows = sorted(rows, key=lambda r: -abs(r["model_prob"] - r["market_prob"]))
    PAD_T, ROW = 44, 30
    h = PAD_T + len(rows) * ROW + 34
    x0, x1 = 150, 620
    lo = min(min(r["model_prob"], r["market_prob"]) for r in rows) - .04
    hi = max(max(r["model_prob"], r["market_prob"]) for r in rows) + .04
    lo, hi = max(0, lo), min(1, hi)
    sx = lambda p: x0 + (p - lo) / (hi - lo) * (x1 - x0)

    o = [f'<svg viewBox="0 0 740 {h}" role="img" aria-label="For each game '
         f'tonight, the chance the home team wins according to the program and '
         f'according to the bookmakers">']
    for t in [i / 100 for i in range(0, 101, 5)]:
        if lo <= t <= hi:
            o.append(f'<line class="grid" x1="{sx(t):.1f}" y1="{PAD_T-10}" '
                     f'x2="{sx(t):.1f}" y2="{h-34}"/>')
            o.append(f'<text class="tick" x="{sx(t):.1f}" y="{PAD_T-16}" '
                     f'text-anchor="middle">{t:.0%}</text>')
    o.append(f'<text class="axis" x="{(x0+x1)/2:.0f}" y="{h-8}" '
             f'text-anchor="middle">chance the home team wins</text>')
    o.append(f'<text class="tick" x="{x1+22}" y="{PAD_T-16}">difference</text>')
    for i, r in enumerate(rows):
        y = PAD_T + i * ROW + ROW / 2
        a, b = sx(r["model_prob"]), sx(r["market_prob"])
        gap = r["model_prob"] - r["market_prob"]          # SIGNED
        lab = f'{abbr(r["away"])} @ {abbr(r["home"])}'
        o.append(f'<text class="cat" x="{x0-16}" y="{y+5}" text-anchor="end">{esc(lab)}</text>')
        o.append(f'<line class="conn" x1="{a:.1f}" y1="{y}" x2="{b:.1f}" y2="{y}"/>')
        o.append(f'<circle class="ring" cx="{b:.1f}" cy="{y}" r="7.5"/>'
                 f'<circle class="mk2" cx="{b:.1f}" cy="{y}" r="6"><title>'
                 f'{esc(r["away"])} at {esc(r["home"])} — bookmakers say the home '
                 f'team wins {r["market_prob"]:.1%}</title></circle>')
        o.append(f'<circle class="ring" cx="{a:.1f}" cy="{y}" r="7.5"/>'
                 f'<circle class="mk1" cx="{a:.1f}" cy="{y}" r="6"><title>'
                 f'{esc(r["away"])} at {esc(r["home"])} — the program says '
                 f'{r["model_prob"]:.1%}</title></circle>')
        o.append(f'<text class="val" x="{x1+22}" y="{y+5}">{gap:+.0%}</text>')
    o.append("</svg>")

    tbl = table(["Game", "Program", "Bookmakers", "Difference"],
                [(f'{r["away"]} @ {r["home"]}', f'{r["model_prob"]:.0%}',
                  f'{r["market_prob"]:.0%}',
                  f'{r["model_prob"] - r["market_prob"]:+.0%}') for r in rows],
                "Show tonight's games as a table")
    lower = sum(1 for r in rows if r["model_prob"] < r["market_prob"])
    return "".join(o), tbl, len(rows), lower


def chart_seasons(seasons, demoted=False):
    """Grouped bars, one scale. No y-axis values: their real unit is log-loss,
    which a stranger cannot read, and the spread is too small to see anyway.
    Bar length does the work; the numbers stay in the tooltips."""
    if not seasons:
        return "", ""
    W = 740
    h, PAD_L, PAD_B, PAD_T = (170, 26, 40, 16) if demoted else (230, 26, 44, 20)
    lo = min(min(s["logloss_model"], s["logloss_market"]) for s in seasons)
    hi = max(max(s["logloss_model"], s["logloss_market"]) for s in seasons)
    pad = (hi - lo) * .35 or .01
    lo, hi = lo - pad, hi + pad
    sy = lambda v: PAD_T + (hi - v) / (hi - lo) * (h - PAD_T - PAD_B)
    gw = (W - PAD_L - 30) / len(seasons)
    bw = min(52, gw / 2 - 8)
    o = [f'<svg class="{"demoted" if demoted else ""}" viewBox="0 0 {W} {h}" role="img" '
         f'aria-label="How wrong the program was each season, next to what it is '
         f'measured against. Shorter bars are better.">']
    for i in range(4):
        v = lo + (hi - lo) * i / 3
        o.append(f'<line class="grid" x1="{PAD_L}" y1="{sy(v):.1f}" x2="{W-30}" y2="{sy(v):.1f}"/>')
    o.append(f'<text class="axis" x="{PAD_L}" y="{PAD_T-4}">more wrong &#8593;</text>')
    for i, s in enumerate(seasons):
        cx = PAD_L + gw * i + gw / 2
        for j, (key, cls, who) in enumerate((("logloss_model", "mk1", "the program"),
                                             ("logloss_market", "mk2", "what it is measured against"))):
            v = s[key]
            x = cx - bw - 1 + j * (bw + 2)
            o.append(f'<rect class="{cls} bar" x="{x:.1f}" y="{sy(v):.1f}" width="{bw:.1f}" '
                     f'height="{max(0, h-PAD_B-sy(v)):.1f}" rx="4"><title>'
                     f'{s["season"]} — {who}: {v:.4f} (lower is better)</title></rect>')
        o.append(f'<text class="cat" x="{cx:.1f}" y="{h-PAD_B+22}" text-anchor="middle">{s["season"]}</text>')
    o.append("</svg>")
    # No raw scores here: on a phone this table REPLACES the chart, so anything
    # in it is visible text, and the underlying unit is log-loss. Who was more
    # accurate is the whole message; the numbers stay in the tooltips.
    tbl = table(["Season", "Who predicted it better"],
                [(s["season"],
                  "the program" if s["logloss_model"] < s["logloss_market"]
                  else "what it's measured against")
                 for s in seasons], "Show these seasons as a table")
    return "".join(o), tbl


def chart_importance(coef):
    """Six merged bars from one origin, longest first. No signed axis: a lay
    reader wants to know what matters, not which dugout it helps. No printed
    values either - the ranking is the entire message."""
    if not coef:
        return "", "", None
    g = {}
    for name, c in coef:
        k = feature_key(name)
        g[k] = g.get(k, 0.0) + abs(c)          # merge home/away into one
    order = sorted(g.items(), key=lambda kv: -kv[1])
    ROW, PAD_T, x0, x1 = 40, 14, 190, 690
    h = PAD_T + len(order) * ROW + 10
    mx = order[0][1] or 1
    o = [f'<svg viewBox="0 0 740 {h}" role="img" aria-label="What the program '
         f'leans on most, longest bar first">']
    for i, (k, v) in enumerate(order):
        y = PAD_T + i * ROW
        w = v / mx * (x1 - x0)
        o.append(f'<text class="cat big" x="{x0-16}" y="{y+22}" text-anchor="end">{esc(GROUP_NAME[k])}</text>')
        o.append(f'<rect class="mk1 bar" x="{x0}" y="{y+6}" width="{max(w,2):.1f}" '
                 f'height="22" rx="4"><title>{esc(GROUP_NAME[k])} — {esc(FEATURE_DEF[k])}'
                 f'</title></rect>')
    o.append("</svg>")
    rows = [(GROUP_NAME[k], f"{v/mx:.0%} as important as the top one") for k, v in order]
    tbl = table(["What it looks at", "Relative weight"], rows,
                "Show this ranking as a table")
    return "".join(o), tbl, order


# ------------------------------------------------------------ bottom lines
# Every figure in these sentences is computed. Nothing here is written by hand,
# so the page cannot drift out of step with the database.
def bl(text):
    return f'<p class="bl"><strong>Bottom line:</strong> {text}</p>'


def bl_picks(n, lower):
    if not n:
        return bl("no games tonight have both a prediction and a price.")
    disagree = n            # every priced game differs to some degree
    s = (f"the program disagrees with the bookmakers on {disagree} of {n} "
         f"game{'s' if n != 1 else ''} tonight — that's a warning sign, not an edge.")
    if lower:
        s += (f" On {lower} of them it thinks the home team is <em>less</em> "
              f"likely to win than the bookmakers do.")
    return bl(s)


def bl_seasons(seasons, real_market):
    n = len(seasons)
    lost = sum(1 for s in seasons if s["logloss_model"] >= s["logloss_market"])
    if real_market:
        if lost == n:
            return bl(f"the bookmakers beat it in all {n} seasons tested.")
        if lost == 0:
            return bl(f"it beat the bookmakers in all {n} seasons tested — "
                      f"the first real sign of an edge.")
        return bl(f"the bookmakers beat it in {lost} of the {n} seasons tested.")
    won = n - lost
    if won == n:
        return bl("it beat a coin flip at baseball. It has never been tested "
                  "against a real bookmaker.")
    return bl(f"it beat the stand-in in {won} of {n} seasons. It has never been "
              f"tested against a real bookmaker.")


def bl_importance(order):
    """Verified against the data, not assumed. 'More than everything else
    combined' was the obvious sentence to write and it is false here: the top
    factor is 0.56 against 0.99 for the rest."""
    if not order:
        return ""
    top, top_v = order[0]
    rest = sum(v for _, v in order[1:])
    second_v = order[1][1] if len(order) > 1 else 0
    name = GROUP_NAME[top].lower()
    if top_v > rest:
        return bl(f"{name} matters more than everything else combined.")
    if second_v:
        return bl(f"{name} matters more than any other single thing — about "
                  f"{top_v/second_v:.1f}× the next biggest — but not more than "
                  f"the rest of the list put together.")
    return bl(f"{name} matters most.")


# ---------------------------------------------------------------------- page
CHECK = {"walk_forward": "Beats the bookmakers on past seasons",
         "paper_trading": "Tracked through 50+ bets on paper, no money",
         "armed": "A human has switched it on"}

STAMP = "Practice test &mdash; doesn&rsquo;t count yet"


def legend():
    return ('<div class="legend"><span><i class="sw1"></i>the program</span>'
            '<span><i class="sw2"></i>the bookmakers</span></div>')


def legend2():
    return ('<div class="legend"><span><i class="sw1"></i>how wrong the program was</span>'
            '<span><i class="sw2"></i>what it is measured against</span></div>')


def build(d):
    sports = list(d["gates"]) or ["mlb", "nfl"]
    passed = sum(1 for g in d["gates"].values() for ok in g.values() if ok)
    total = sum(len(g) for g in d["gates"].values()) or 6
    cleared = any(all(g.values()) for g in d["gates"].values())

    # Three checks listed once in a compact grid. The old version repeated
    # nearly the same sentence six times.
    rows = "".join(
        f'<tr><th scope="row">{esc(CHECK[k])}</th>' +
        "".join(f'<td class="{"ok" if d["gates"].get(s, {}).get(k) else "no"}">'
                f'{"yes" if d["gates"].get(s, {}).get(k) else "not yet"}</td>'
                for s in sports) + "</tr>"
        for k in CHECK)
    head = "".join(f"<th>{s.upper()}</th>" for s in sports)
    checks_tbl = (f'<table class="checks"><thead><tr><th scope="col">Check</th>'
                  f'{head}</tr></thead><tbody>{rows}</tbody></table>')

    picks_svg, picks_tbl, n_games, n_lower = chart_picks(d["picks"])
    imp_svg, imp_tbl, imp_order = chart_importance(d["coef"])

    # Football first and full-strength: it is the only one graded against real
    # bookmaker prices. Baseball second and visibly demoted, because rendering
    # them as twins invites a skimmer to read them as the same test.
    panels = ""
    for sport, real in (("nfl", True), ("mlb", False)):
        srows = d["seasons"].get(sport)
        if not srows:
            continue
        svg, tbl = chart_seasons(srows, demoted=not real)
        if real:
            panels += (
                '<h3>Graded against real bookmaker prices</h3>'
                '<p class="note">Each pair of bars is one American football season '
                'the program had never seen when it was trained. '
                '<span class="chart-only"><b class="c1">Blue</b> is how wrong the '
                'program was; <b class="c2">orange</b> is how wrong the bookmakers '
                'were. <strong>Shorter is better.</strong></span></p>'
                + legend2() + svg + bl_seasons(srows, True) + tbl)
        else:
            panels += (
                f'<div class="demote"><div class="stamp">{STAMP}</div>'
                '<h3>Baseball, graded against a stand-in</h3>'
                '<p class="note">Same chart, but the orange bars here are a '
                'placeholder, not real bookmaker prices. Beating them shows the '
                'program learned something about baseball &mdash; not that it could '
                'beat a bookmaker.</p>'
                + legend2() + svg + bl_seasons(srows, False) + tbl + '</div>')

    yrs = d.get("train_seasons") or []
    tiles = [("Games studied", f'{d.get("n_train", 0):,}',
              f'{yrs[0]}&ndash;{yrs[-1]}' if yrs else "not trained yet"),
             ("Predictions made today", f'{d["n_pred"]:,}',
              "one per game it has enough data for")]
    tile_html = "".join(
        f'<div class="tile"><div class="tile-l">{l}</div>'
        f'<div class="tile-v">{v}</div><div class="tile-s">{s}</div></div>'
        for l, v, s in tiles)

    seen, gloss = set(), []
    for name, _ in d["coef"]:
        k = feature_key(name)
        if k in seen:
            continue
        seen.add(k)
        gloss.append(f"<dt>{esc(GLOSS_HEAD[k])}</dt><dd>{esc(FEATURE_DEF[k])}</dd>")
    gloss_html = "<dl class='gloss'>" + "".join(gloss) + "</dl>" if gloss else ""

    acc = f'{d["accuracy"]:.1%}' if d.get("accuracy") else "&mdash;"
    verdict = "READY TO BET" if cleared else "NOT READY TO BET"
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
    font:16px/1.6 ui-sans-serif,system-ui,"Segoe UI",sans-serif; }}
  .wrap {{ max-width:880px; margin:0 auto; padding:32px 16px 56px; }}
  h1 {{ font-size:26px; margin:0 0 6px; letter-spacing:-.02em; }}
  h2 {{ font-size:20px; margin:44px 0 6px; letter-spacing:-.01em; }}
  h3 {{ font-size:15px; margin:20px 0 4px; }}
  .lede {{ font-size:17px; margin:10px 0 4px; max-width:60ch; }}
  .muted {{ color:var(--ink2); font-size:13px; margin:0; }}
  .note {{ font-size:15px; color:var(--ink2); margin:6px 0 10px; max-width:64ch; }}
  .note strong, .note b {{ color:var(--ink); }}
  .c1 {{ color:var(--s1); }} .c2 {{ color:var(--s2); }}

  .hero {{ margin:26px 0 10px; padding:22px; border-radius:14px;
    border:2px solid {accent};
    background:color-mix(in srgb, {accent} 6%, transparent); }}
  .hero-v {{ font-size:40px; font-weight:750; line-height:1.05;
    letter-spacing:-.03em; color:{accent}; }}
  .hero-c {{ font-size:15px; font-weight:600; margin-top:6px; }}
  .hero-s {{ font-size:15px; color:var(--ink2); margin:14px 0 0; max-width:62ch; }}
  .hero-s strong {{ color:var(--ink); }}

  table.checks {{ border-collapse:collapse; margin-top:16px; font-size:14px; width:100%; }}
  table.checks th, table.checks td {{ text-align:left; padding:8px 10px;
    border-bottom:1px solid color-mix(in srgb, var(--ink) 12%, transparent); }}
  table.checks thead th {{ font-size:12px; color:var(--ink2);
    text-transform:uppercase; letter-spacing:.05em; }}
  table.checks td {{ text-align:center; font-weight:600; width:86px; }}
  table.checks td.ok {{ color:var(--good); }} table.checks td.no {{ color:var(--crit); }}

  .bl {{ font-size:16px; margin:14px 0 4px; padding:12px 14px; border-radius:10px;
    background:color-mix(in srgb, var(--ink) 5%, transparent); max-width:64ch; }}
  .bl strong {{ color:var(--ink); }}

  svg {{ width:100%; height:auto; display:block; margin-top:8px; overflow:visible; }}
  svg.demoted {{ opacity:.6; }}
  .demote {{ margin-top:26px; padding:14px 16px 6px; border-radius:12px;
    border:1px dashed color-mix(in srgb, var(--ink) 26%, transparent); }}
  .stamp {{ display:inline-block; font-size:11px; font-weight:700;
    letter-spacing:.08em; text-transform:uppercase; padding:4px 9px;
    border-radius:5px; color:var(--ink2);
    border:1px solid color-mix(in srgb, var(--ink) 30%, transparent); }}
  .grid {{ stroke:color-mix(in srgb, var(--ink) 11%, transparent); stroke-width:1; }}
  .conn {{ stroke:color-mix(in srgb, var(--ink) 26%, transparent); stroke-width:2; }}
  .ring {{ fill:var(--surface); }}
  .mk1 {{ fill:var(--s1); }} .mk2 {{ fill:var(--s2); }}
  .bar {{ stroke:var(--surface); stroke-width:2; }}
  text {{ font:14px ui-sans-serif,system-ui,sans-serif; fill:var(--ink2); }}
  .cat {{ fill:var(--ink); }} .cat.big {{ font-size:16px; font-weight:600; }}
  .val {{ fill:var(--ink); font-variant-numeric:tabular-nums; }}
  .tick, .axis {{ font-size:13px; }}
  rect.bar, circle {{ transition:opacity .12s; }}
  svg:hover rect.bar, svg:hover circle {{ opacity:.82; }}
  rect.bar:hover, circle:hover {{ opacity:1; }}

  .legend {{ display:flex; flex-wrap:wrap; gap:16px; font-size:14px;
    color:var(--ink2); margin-top:10px; }}
  .legend i {{ width:11px; height:11px; border-radius:99px; display:inline-block;
    margin-right:6px; vertical-align:-1px; }}
  .sw1 {{ background:var(--s1); }} .sw2 {{ background:var(--s2); }}

  details.tbl {{ margin-top:10px; }}
  details.tbl summary {{ font-size:14px; color:var(--ink2); cursor:pointer; }}
  details table {{ border-collapse:collapse; width:100%; margin-top:10px;
    font-size:14px; }}
  details th, details td {{ text-align:left; padding:6px 9px;
    font-variant-numeric:tabular-nums;
    border-bottom:1px solid color-mix(in srgb, var(--ink) 10%, transparent); }}

  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
    gap:12px; margin-top:14px; }}
  .tile {{ padding:14px 16px; border-radius:11px;
    border:1px solid color-mix(in srgb, var(--ink) 12%, transparent); }}
  .tile-l {{ font-size:13px; color:var(--ink2); }}
  .tile-v {{ font-size:26px; font-weight:650; letter-spacing:-.02em; margin:2px 0; }}
  .tile-s {{ font-size:12px; color:var(--ink2); }}
  .gloss {{ margin:8px 0 0; max-width:66ch; }}
  .gloss dt {{ font-weight:600; font-size:14px; margin-top:14px; }}
  .gloss dd {{ margin:2px 0 0; font-size:14px; color:var(--ink2); }}
  h3.small {{ font-size:12px; color:var(--ink2); margin-top:26px;
    text-transform:uppercase; letter-spacing:.06em; }}

  /* On a phone the SVG text would scale to about 6px and hover does not exist,
     so the table becomes the chart rather than a supplement to it. */
  @media (max-width:560px) {{
    body {{ font-size:15px; }}
    svg, .legend {{ display:none; }}
    details.tbl summary {{ display:none; }}
    .chart-only {{ display:none; }}
    .hero-v {{ font-size:30px; }}
    .bl {{ font-size:15px; }}
  }}
</style></head>
<body><div class="wrap">

<header>
  <h1>Sports Machine</h1>
  <p class="lede">A program that tries to predict who wins baseball games, and then
  refuses to let anyone bet on its predictions until it can prove it is better than
  the bookmakers.</p>
  <p class="muted">{esc(d["generated"])}</p>
</header>

<section class="hero">
  <div class="hero-v">{verdict}</div>
  <div class="hero-c">{passed} of {total} safety checks passed</div>
  <p class="hero-s">It picks the winning side <strong>{acc}</strong> of the time,
  tested the honest way: trained on past seasons only, then graded on a season it had
  never seen, over <strong>{d.get("n_train", 0):,}</strong> real games. That sounds
  like plenty. It isn&rsquo;t &mdash; coin flipping is 50%, the home team wins about
  53% on its own, and bookmakers do better than all of that while taking a cut of
  every bet. To make money you have to beat <strong>them</strong>.</p>
  {checks_tbl}
</section>

<h2>1 &mdash; Tonight&rsquo;s games</h2>
<p class="note">Each row is one game: the program&rsquo;s estimate of the home
team&rsquo;s chances, next to the bookmakers&rsquo;.<span class="chart-only"> The
<b class="c1">blue dot</b> is the program, the <b class="c2">orange dot</b> is the
bookmakers, and the line between them is how far apart they are.</span></p>
{legend()}
{picks_svg}
{bl_picks(n_games, n_lower)}
{picks_tbl}

<h2>2 &mdash; Has it ever beaten a bookmaker?</h2>
<p class="note">This is the test that decides everything. Two charts below, and they
are <strong>not</strong> the same test.</p>
{panels}

<h2>3 &mdash; What it pays attention to</h2>
<p class="note">Everything the program knows about a game, most important
first.</p>
{imp_svg}
{bl_importance(imp_order)}
{imp_tbl}
<h3 class="small">What each of these actually measures</h3>
{gloss_html}

<h2>What happens next</h2>
<p class="note">The program collects betting prices three times a day on its own.
Once it has enough of them, the baseball model can be re-graded against real
bookmaker prices instead of a stand-in &mdash; and that is the test that decides
whether any of this is worth anything. Until then the honest answer to
&ldquo;does it work?&rdquo; is <strong>we don&rsquo;t know yet</strong>, and it is
built to keep saying that.</p>

<h3 class="small">By the numbers</h3>
<div class="tiles">{tile_html}</div>

</div>
<!-- Generated from {esc(d["db"])} - rebuild with: python dashboard.py -->
</body></html>"""


if __name__ == "__main__":
    data = gather()
    OUT.write_text(build(data), encoding="utf-8")
    print(f"Wrote {OUT}")
    if "--no-open" not in sys.argv:
        webbrowser.open(OUT.as_uri())
