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
SPORT_NAME = {"mlb": "Baseball", "nfl": "Football"}

GROUP_NAME = {"sp": "Who's pitching", "off": "Recent hitting",
              "pen_q": "Bullpen quality", "park": "The ballpark",
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


_TID = [0]


def table(headers, rows, caption):
    """Collapsed on desktop, forced open on a phone - where it REPLACES the
    chart, because SVG text at 375px lands near 6px and hover does not exist.

    A <details> cannot do that: it collapses its content whatever CSS the child
    carries, so a media query cannot open one. This is the checkbox-and-label
    pattern instead - pure CSS, no script, and the breakpoint genuinely works.
    """
    _TID[0] += 1
    tid = f"t{_TID[0]}"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>"
                   for r in rows) or f'<tr><td colspan="{len(headers)}">nothing yet</td></tr>'
    return (f'<input type="checkbox" id="{tid}" class="tgl">'
            f'<label for="{tid}">{esc(caption)}</label>'
            f'<div class="tblbox"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def gap_words(gaps):
    """Rank each season's gap against the others. An absolute size word came out
    as "clearly" in all four rows - no information at all, and on a phone this
    table replaces the chart, so it was the only thing a mobile reader got."""
    lo, hi = min(gaps), max(gaps)
    if hi - lo < 1e-9:
        return ["about the same each year"] * len(gaps)
    out = []
    for g in gaps:
        n = (g - lo) / (hi - lo)
        out.append(f"widest of the {len(gaps)}" if n > .99
                   else ("narrowest" if n < .01 else "in between"))
    return out


# -------------------------------------------------------------------- charts
def chart_picks(rows):
    """Dumbbell, one row per game. The gap is signed (program - bookmakers):
    unsigned it read '+10%' on every row even though the program sits BELOW the
    bookmakers on most home teams, which is the pattern worth seeing."""
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
        gap = r["model_prob"] - r["market_prob"]
        lab = f'{abbr(r["away"])} @ {abbr(r["home"])}'
        o.append(f'<text class="cat" x="{x0-16}" y="{y+5}" text-anchor="end">{esc(lab)}</text>')
        o.append(f'<line class="conn" x1="{a:.1f}" y1="{y}" x2="{b:.1f}" y2="{y}"/>')
        o.append(f'<circle class="ring" cx="{b:.1f}" cy="{y}" r="7.5"/>'
                 f'<circle class="mk2" cx="{b:.1f}" cy="{y}" r="6"><title>'
                 f'{esc(r["away"])} at {esc(r["home"])} — bookmakers: home team '
                 f'wins {r["market_prob"]:.1%}</title></circle>')
        o.append(f'<circle class="ring" cx="{a:.1f}" cy="{y}" r="7.5"/>'
                 f'<circle class="mk1" cx="{a:.1f}" cy="{y}" r="6"><title>'
                 f'{esc(r["away"])} at {esc(r["home"])} — the program: '
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


def chart_seasons(seasons, opponent):
    """Grouped bars, one scale, no y-axis values - the unit is log-loss, which
    a stranger cannot read, and the spread is too small to see. Bar length
    carries it; the numbers stay in the tooltips."""
    if not seasons:
        return "", ""
    W, h, PAD_L, PAD_B, PAD_T = 740, 230, 26, 44, 20
    lo = min(min(s["logloss_model"], s["logloss_market"]) for s in seasons)
    hi = max(max(s["logloss_model"], s["logloss_market"]) for s in seasons)
    pad = (hi - lo) * .35 or .01
    lo, hi = lo - pad, hi + pad
    sy = lambda v: PAD_T + (hi - v) / (hi - lo) * (h - PAD_T - PAD_B)
    gw = (W - PAD_L - 30) / len(seasons)
    bw = min(52, gw / 2 - 8)
    o = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="How wrong the '
         f'program was each season next to {esc(opponent)}. Shorter is better.">']
    for i in range(4):
        v = lo + (hi - lo) * i / 3
        o.append(f'<line class="grid" x1="{PAD_L}" y1="{sy(v):.1f}" x2="{W-30}" y2="{sy(v):.1f}"/>')
    o.append(f'<text class="axis" x="{PAD_L}" y="{PAD_T-4}">more wrong &#8593;</text>')
    for i, s in enumerate(seasons):
        cx = PAD_L + gw * i + gw / 2
        for j, (key, cls, who) in enumerate((("logloss_model", "mk1", "the program"),
                                             ("logloss_market", "mk2", opponent))):
            v = s[key]
            x = cx - bw - 1 + j * (bw + 2)
            o.append(f'<rect class="{cls} bar" x="{x:.1f}" y="{sy(v):.1f}" width="{bw:.1f}" '
                     f'height="{max(0, h-PAD_B-sy(v)):.1f}" rx="4"><title>'
                     f'{s["season"]} — {esc(who)}: {v:.4f} (lower is better)</title></rect>')
        o.append(f'<text class="cat" x="{cx:.1f}" y="{h-PAD_B+22}" text-anchor="middle">{s["season"]}</text>')
    o.append("</svg>")
    gaps = [abs(s["logloss_market"] - s["logloss_model"])
            / max(s["logloss_model"], s["logloss_market"]) for s in seasons]
    words = gap_words(gaps)
    rows = [(s["season"],
             "the program" if s["logloss_model"] < s["logloss_market"] else opponent,
             w)
            for s, w in zip(seasons, words)]
    tbl = table(["Season", "Who predicted it better", "Size of the gap"], rows,
                "Show these seasons as a table")
    return "".join(o), tbl


def chart_importance(coef):
    """Six merged bars from one origin, longest first. No signed axis and no
    printed values: the ranking is the entire message."""
    if not coef:
        return "", "", None
    g = {}
    for name, c in coef:
        g[feature_key(name)] = g.get(feature_key(name), 0.0) + abs(c)
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
    tbl = table(["What it looks at", "As important as the top one"],
                [(GROUP_NAME[k], f"{v/mx:.0%}") for k, v in order],
                "Show this ranking as a table")
    return "".join(o), tbl, order


# ------------------------------------------------------------ bottom lines
# Declarative. Every figure computed, nothing written by hand, so the page
# cannot drift out of step with the database.
def bl(text):
    return f'<p class="bl"><strong>Bottom line:</strong> {text}</p>'


def bl_picks(n, lower):
    if not n:
        return ""
    s = f"it disagrees with the bookmakers on all {n} games tonight"
    if lower:
        s += f", and on {lower} of them it rates the home team lower than they do"
    return bl(s + ".")


def bl_seasons(seasons, opponent):
    n = len(seasons)
    lost = sum(1 for s in seasons if s["logloss_model"] >= s["logloss_market"])
    if lost == n:
        return bl(f"{opponent} predicted every one of the {n} seasons better "
                  f"than the program did.")
    if lost == 0:
        return bl(f"the program predicted all {n} seasons better than "
                  f"{opponent} did.")
    return bl(f"{opponent} predicted {lost} of the {n} seasons better.")


def bl_importance(order):
    """'More than everything else combined' was the obvious sentence and it is
    false here - the top factor is 0.56 against 0.99 for the rest - so the
    claim is checked before it is emitted."""
    if not order:
        return ""
    top, top_v = order[0]
    rest = sum(v for _, v in order[1:])
    second = order[1][1] if len(order) > 1 else 0
    name = GROUP_NAME[top].lower()
    if top_v > rest:
        return bl(f"{name} matters more than everything else combined.")
    if second:
        return bl(f"{name} matters more than any other single thing, about "
                  f"{top_v/second:.1f}× the next biggest.")
    return bl(f"{name} matters most.")


# ---------------------------------------------------------------------- page
CHECK = {"walk_forward": "Beats the bookmakers on past seasons",
         "paper_trading": "Tracked through 50+ bets on paper, no money",
         "armed": "A human has switched it on"}


def legend(a, b):
    return (f'<div class="legend"><span><i class="sw1"></i>{a}</span>'
            f'<span><i class="sw2"></i>{b}</span></div>')


def build(d):
    sports = list(d["gates"]) or ["mlb", "nfl"]
    cleared = any(all(g.values()) for g in d["gates"].values())
    # The reader counts the rows in the table, so the headline has to count the
    # same way: three checks, not three-times-two. Computed, so it still reads
    # correctly if the sports ever diverge.
    n_checks = len(CHECK)
    per = {sp: sum(1 for k in CHECK if d["gates"].get(sp, {}).get(k)) for sp in sports}
    agree = all(d["gates"].get(sports[0], {}).get(k) == d["gates"].get(sp, {}).get(k)
                for k in CHECK for sp in sports)
    if agree:
        count_line = (f"{per[sports[0]]} of {n_checks} passed, "
                      f"for {'either' if len(sports) == 2 else 'every'} sport")
    else:
        count_line = " &middot; ".join(
            f"{SPORT_NAME.get(sp, sp.upper())} {per[sp]} of {n_checks}" for sp in sports)

    rows = "".join(
        f'<tr><th scope="row">{esc(CHECK[k])}</th>' +
        "".join(f'<td class="{"ok" if d["gates"].get(s, {}).get(k) else "no"}">'
                f'{"yes" if d["gates"].get(s, {}).get(k) else "not yet"}</td>'
                for s in sports) + "</tr>"
        for k in CHECK)
    head = "".join(f"<th>{SPORT_NAME.get(s, s.upper())}</th>" for s in sports)
    checks_tbl = (f'<table class="checks"><thead><tr><th scope="col">Check</th>'
                  f'{head}</tr></thead><tbody>{rows}</tbody></table>')

    picks_svg, picks_tbl, n_games, n_lower = chart_picks(d["picks"])
    imp_svg, imp_tbl, imp_order = chart_importance(d["coef"])

    # Only the football model is graded against real bookmaker prices, so it is
    # the only season chart on the page. The baseball one was scored against a
    # placeholder - it proved nothing the page is asking about, and cost a
    # heading, a stamp, two notes, a legend, an SVG, a bottom line and a table
    # to say something that fits in one sentence.
    nfl_rows = d["seasons"].get("nfl") or []
    OPP = "the bookmakers"
    nfl_svg, nfl_tbl = chart_seasons(nfl_rows, OPP) if nfl_rows else ("", "")
    # Setup, then chart, then the bottom line LAST in the section.
    season_block = ('<p class="note">&ldquo;How wrong&rdquo; counts whether it '
                    'picked the right side and how sure it was, so confident '
                    'mistakes cost most.</p>'
                    + legend("how wrong the program was", OPP) + nfl_svg
                    + nfl_tbl + bl_seasons(nfl_rows, OPP)) if nfl_rows else ""

    # 10,482 was printed in the hero and again in a tile. The tiles are gone;
    # the one fact worth keeping moves to the section it describes.
    priced_note = (f'<p class="note">{n_games} of today&rsquo;s {d["n_pred"]} '
                   f'predicted games have a bookmaker price so far.</p>'
                   if d["n_pred"] > n_games else "")

    seen, gloss = set(), []
    for name, _ in d["coef"]:
        k = feature_key(name)
        if k in seen:
            continue
        seen.add(k)
        gloss.append(f"<dt>{esc(GROUP_NAME[k])}</dt><dd>{esc(FEATURE_DEF[k])}</dd>")
    gloss_html = ('<details class="gloss-d"><summary>What each of these actually '
                  'measures</summary><dl class="gloss">' + "".join(gloss)
                  + "</dl></details>") if gloss else ""

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
  .lede {{ font-size:17px; margin:10px 0 4px; max-width:62ch; }}
  .muted {{ color:var(--ink2); font-size:13px; margin:0; }}
  .note {{ font-size:15px; color:var(--ink2); margin:8px 0 10px; max-width:64ch; }}
  .note strong, .note b {{ color:var(--ink); }}

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
  table.checks td {{ text-align:center; font-weight:600; width:92px; }}
  table.checks td.ok {{ color:var(--good); }} table.checks td.no {{ color:var(--crit); }}

  .primer {{ margin:30px 0 0; padding:16px 18px; border-radius:12px;
    border-left:3px solid var(--s2);
    background:color-mix(in srgb, var(--s2) 6%, transparent); }}
  .primer p {{ font-size:15px; color:var(--ink2); margin:0; max-width:62ch; }}

  .bl {{ font-size:16px; margin:14px 0 4px; padding:12px 14px; border-radius:10px;
    background:var(--box); border:1px solid var(--boxline); max-width:64ch; }}
  .bl strong {{ color:var(--ink); }}
  /* Windows high-contrast strips backgrounds; keep the box readable as a box. */
  @media (forced-colors: active) {{
    .bl {{ border:1px solid CanvasText; background:Canvas; }}
  }}

  svg {{ width:100%; height:auto; display:block; margin-top:8px; overflow:visible; }}
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

  /* Checkbox toggle, not <details>: a media query cannot open a <details>,
     and on a phone the table has to REPLACE the chart. */
  .tgl {{ position:absolute; opacity:0; width:0; height:0; }}
  .tgl + label {{ display:inline-block; margin-top:12px; font-size:14px;
    color:var(--ink2); cursor:pointer; border-bottom:1px dotted currentColor; }}
  .tgl:focus-visible + label {{ outline:2px solid var(--s1); outline-offset:3px; }}
  .tblbox {{ display:none; }}
  .tgl:checked ~ .tblbox {{ display:block; }}
  .tblbox table {{ border-collapse:collapse; width:100%; margin-top:10px;
    font-size:14px; }}
  .tblbox th, .tblbox td {{ text-align:left; padding:6px 9px;
    font-variant-numeric:tabular-nums;
    border-bottom:1px solid color-mix(in srgb, var(--ink) 10%, transparent); }}

  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
    gap:12px; margin-top:14px; }}
  .tile {{ padding:14px 16px; border-radius:11px;
    border:1px solid color-mix(in srgb, var(--ink) 12%, transparent); }}
  .tile-l {{ font-size:13px; color:var(--ink2); }}
  .tile-v {{ font-size:26px; font-weight:650; letter-spacing:-.02em; margin:2px 0; }}
  .tile-s {{ font-size:12px; color:var(--ink2); }}
  .gloss-d {{ margin-top:12px; }}
  .gloss-d summary {{ font-size:14px; color:var(--ink2); cursor:pointer; }}
  .gloss {{ margin:10px 0 0; max-width:66ch; }}
  .gloss dt {{ font-weight:600; font-size:14px; margin-top:14px; }}
  .gloss dd {{ margin:2px 0 0; font-size:14px; color:var(--ink2); }}
  h3.small {{ font-size:12px; color:var(--ink2); margin-top:30px;
    text-transform:uppercase; letter-spacing:.06em; }}

  @media (max-width:560px) {{
    body {{ font-size:15px; }}
    svg, .legend, .chart-only {{ display:none; }}
    .tgl + label {{ display:none; }}
    .tblbox {{ display:block; }}
    .hero-v {{ font-size:30px; }}
    .bl {{ font-size:15px; }}
  }}
</style></head>
<body><div class="wrap">

<header>
  <h1>Sports Machine</h1>
  <p class="lede">Predicts who wins baseball games. It will not place a bet until it
  can prove it beats the bookmakers &mdash; three checks, enforced in code.</p>
  <p class="muted">{esc(d["generated"])}</p>
</header>

<section class="hero">
  <div class="hero-v">{verdict}</div>
  <div class="hero-c">{count_line}</div>
  <p class="hero-s">It picks the winning side <strong>{acc}</strong> of the time,
  over <strong>{d.get("n_train", 0):,}</strong> games it had never seen when it was
  trained. Coin flipping is 50% and the home team wins about 53% for free.
  Bookmakers beat both, and take a cut of every bet.</p>
  {checks_tbl}
  <p class="hero-s">A program can be tuned until it looks good on seasons it has
  already seen, so the second check makes it predict games that have not happened
  yet. The third is a manual switch, so nothing can start betting by accident.</p>
</section>

<section class="primer">
  <p>A betting line is a prediction: a bookmaker&rsquo;s price converts directly
  into a percentage chance, which is all the orange numbers here are. Those
  percentages are extremely hard to beat, because they absorb every injury report
  and every dollar wagered within minutes.</p>
</section>

<h2>1 &mdash; Has it ever beaten a bookmaker?</h2>
<p class="note">Baseball can&rsquo;t sit this test yet; its prices are only being
collected now, three times a day. So here is the same program on American football,
where years of real bookmaker prices are on public
record.<span class="chart-only"> Shorter is better.</span></p>
{season_block}

<h2>2 &mdash; Tonight&rsquo;s games</h2>
{priced_note}{legend("the program", "the bookmakers")}
{picks_svg}
{picks_tbl}
{bl_picks(n_games, n_lower)}

<h2>3 &mdash; What it pays attention to</h2>
{imp_svg}
{imp_tbl}
{gloss_html}
{bl_importance(imp_order)}

<h2>What happens next</h2>
<p class="note">The program collects betting prices three times a day on its own.
Once it has enough, the baseball model can be graded against real bookmaker prices
&mdash; the test that decides whether it can bet. Until then it stays locked.</p>

</div>
<!-- Generated from {esc(d["db"])} - rebuild with: python dashboard.py -->
</body></html>"""


# ----------------------------------------------------------------- picture
PNG = ROOT / "dashboard.png"

# Edge ships with Windows and Chrome is common; either can screenshot a local
# file headlessly. That keeps this dependency-free - no playwright, no
# selenium, nothing to install.
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
        found = shutil.which(name)
        if found:
            return found
    return None


def to_png(width: int = 900, tall: int = 6000, pad: int = 28) -> Path | None:
    """Screenshot the page, then trim the empty space below the content.

    A headless shot is a fixed window, so it is rendered deliberately too tall
    and cropped back to where the content actually ends - otherwise every image
    carries a few thousand pixels of blank page.
    """
    import subprocess
    browser = find_browser()
    if not browser:
        print("No Edge or Chrome found, so no PNG. The HTML still works.")
        return None
    subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--force-color-profile=srgb", f"--screenshot={PNG}",
         f"--window-size={width},{tall}", OUT.as_uri()],
        capture_output=True, timeout=120)
    if not PNG.exists():
        print("The browser did not produce an image.")
        return None
    try:
        from PIL import Image
    except ImportError:
        print(f"Wrote {PNG} (uncropped - install Pillow to trim the blank tail)")
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
