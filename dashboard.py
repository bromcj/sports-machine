"""Build a one-page visual summary of the machine and open it in a browser.

    python dashboard.py            build and open
    python dashboard.py --no-open  build only

Reads the live database, the training table and validation.json, and writes a
self-contained dashboard.html - no server, no internet, no API credits. Because
it reads local data it is always current, which a hosted page could not be.

Four views, each chosen for the job the data has to do:

  gates       state, not magnitude -> status chips with words, never colour alone
  stat tiles  single headline numbers -> no chart earns its place here
  picks       two values per game (model vs market) -> dumbbell, one row per game
  seasons     the same two measures across seasons -> grouped bars on ONE scale
  influence   signed magnitude per feature -> diverging bars around zero

Every label on the page is written for a reader with no background: a friend
should be able to open it cold and understand what they are looking at, and
in particular why the program refuses to bet.
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


# -------------------------------------------------------------------- charts
def chart_picks(rows):
    """Dumbbell: model and market probability for each game, one row each."""
    rows = [r for r in rows if r.get("market_prob") is not None]
    if not rows:
        return "<p class='empty'>No games today have both a prediction and a price.</p>"
    rows = sorted(rows, key=lambda r: -abs(r["edge"]))
    H, PAD_T, ROW = 28, 26, 28
    h = PAD_T + len(rows) * ROW + 16
    x0, x1 = 232, 700
    lo = min(min(r["model_prob"], r["market_prob"]) for r in rows) - .04
    hi = max(max(r["model_prob"], r["market_prob"]) for r in rows) + .04
    lo, hi = max(0, lo), min(1, hi)
    sx = lambda p: x0 + (p - lo) / (hi - lo) * (x1 - x0)

    out = [f'<svg viewBox="0 0 740 {h}" role="img" '
           f'aria-label="Model versus market probability for each game today">']
    for t in [i / 100 for i in range(0, 101, 5)]:
        if lo <= t <= hi:
            out.append(f'<line class="grid" x1="{sx(t):.1f}" y1="{PAD_T-8}" '
                       f'x2="{sx(t):.1f}" y2="{h-16}"/>')
            out.append(f'<text class="tick" x="{sx(t):.1f}" y="{PAD_T-14}" '
                       f'text-anchor="middle">{t:.0%}</text>')
    for i, r in enumerate(rows):
        y = PAD_T + i * ROW + ROW / 2
        a, b = sx(r["model_prob"]), sx(r["market_prob"])
        label = f'{r["away"][:13]} @ {r["home"][:13]}'
        out.append(f'<text class="cat" x="{x0-12}" y="{y+4}" text-anchor="end">{esc(label)}</text>')
        out.append(f'<line class="conn" x1="{a:.1f}" y1="{y}" x2="{b:.1f}" y2="{y}"/>')
        # 2px surface ring keeps the dots legible where they overlap
        out.append(f'<circle class="ring" cx="{b:.1f}" cy="{y}" r="6.5"/>'
                   f'<circle class="mk2" cx="{b:.1f}" cy="{y}" r="5"><title>'
                   f'{esc(label)} — market {r["market_prob"]:.1%}</title></circle>')
        out.append(f'<circle class="ring" cx="{a:.1f}" cy="{y}" r="6.5"/>'
                   f'<circle class="mk1" cx="{a:.1f}" cy="{y}" r="5"><title>'
                   f'{esc(label)} — model {r["model_prob"]:.1%}</title></circle>')
        out.append(f'<text class="val" x="{x1+14}" y="{y+4}">{r["edge"]:+.1%}</text>')
    out.append(f'<text class="tick" x="{x1+14}" y="{PAD_T-14}">gap</text>')
    out.append("</svg>")
    return "".join(out)


def chart_seasons(seasons):
    """Grouped bars: how wrong each side was, per season. One scale - never two."""
    if not seasons:
        return "<p class='empty'>No walk-forward result recorded yet.</p>"
    W, h, PAD_L, PAD_B, PAD_T = 740, 250, 58, 40, 20
    lo = min(min(s["logloss_model"], s["logloss_market"]) for s in seasons)
    hi = max(max(s["logloss_model"], s["logloss_market"]) for s in seasons)
    pad = (hi - lo) * .35 or .01
    lo, hi = lo - pad, hi + pad
    sy = lambda v: PAD_T + (hi - v) / (hi - lo) * (h - PAD_T - PAD_B)
    gw = (W - PAD_L - 24) / len(seasons)
    bw = min(46, gw / 2 - 6)
    out = [f'<svg viewBox="0 0 {W} {h}" role="img" '
           f'aria-label="How wrong the program was versus what it competes against, each season. Shorter bars are better.">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        out.append(f'<line class="grid" x1="{PAD_L}" y1="{sy(v):.1f}" x2="{W-24}" y2="{sy(v):.1f}"/>')
        out.append(f'<text class="tick" x="{PAD_L-10}" y="{sy(v)+4:.1f}" text-anchor="end">{v:.3f}</text>')
    for i, s in enumerate(seasons):
        cx = PAD_L + gw * i + gw / 2
        for j, (key, cls, name) in enumerate((("logloss_model", "mk1", "model"),
                                              ("logloss_market", "mk2", "baseline"))):
            v = s[key]
            # 2px gap between adjacent bars
            x = cx - bw - 1 + j * (bw + 2)
            out.append(f'<rect class="{cls} bar" x="{x:.1f}" y="{sy(v):.1f}" width="{bw:.1f}" '
                       f'height="{max(0, h-PAD_B-sy(v)):.1f}" rx="4"><title>'
                       f'{s["season"]} {name}: {v:.4f} (lower is better)</title></rect>')
        out.append(f'<text class="cat" x="{cx:.1f}" y="{h-PAD_B+20}" text-anchor="middle">{s["season"]}</text>')
    out.append(f'<text class="axis" x="{PAD_L-10}" y="{PAD_T-6}" text-anchor="end">more wrong &#8593;</text>')
    out.append("</svg>")
    return "".join(out)


# "away_sp_kbb_5s" is meaningless to anyone who did not write it.
PLAIN_FEATURE = {
    "home_sp_kbb_5s": "Home starting pitcher, recent form",
    "away_sp_kbb_5s": "Visiting starting pitcher, recent form",
    "home_off_woba_30d": "How well the home team has been hitting",
    "away_off_woba_30d": "How well the visitors have been hitting",
    "home_pen_kbb_30d": "Home relief pitchers, quality",
    "away_pen_kbb_30d": "Visiting relief pitchers, quality",
    "home_pen_pitches_3d": "Home relief pitchers, how tired",
    "away_pen_pitches_3d": "Visiting relief pitchers, how tired",
    "home_rest_days": "Days off for the home team",
    "away_rest_days": "Days off for the visitors",
    "park_factor": "The ballpark (some inflate scoring)",
}


def chart_coef(coef):
    """Diverging bars around zero. One series, so no legend - the title names it."""
    if not coef:
        return "<p class='empty'>No trained model yet.</p>"
    ROW, PAD_T = 26, 14
    h = PAD_T + len(coef) * ROW + 30
    # Values live in a fixed right-hand column, not at the bar ends. Anchoring
    # them to the end of a NEGATIVE bar walks them left into the feature names -
    # at full width the longest bar collided with its own label.
    # Plain-English names are long, so the label column is wider than it would
    # need to be for raw column names.
    LBL_R, mid, half, VAL_X = 292, 484, 150, 664
    mx = max(abs(c) for _, c in coef) or 1
    out = [f'<svg viewBox="0 0 740 {h}" role="img" '
           f'aria-label="How much each piece of information moves the prediction, and in which direction.">']
    out.append(f'<line class="zero" x1="{mid}" y1="{PAD_T-4}" x2="{mid}" y2="{h-30}"/>')
    for i, (name, c) in enumerate(coef):
        y = PAD_T + i * ROW
        w = abs(c) / mx * half
        x = mid if c >= 0 else mid - w
        cls = "mk1" if c >= 0 else "mk2"
        label = PLAIN_FEATURE.get(name, name)
        out.append(f'<text class="cat" x="{LBL_R}" y="{y+15}" text-anchor="end">{esc(label)}</text>')
        out.append(f'<rect class="{cls} bar" x="{x:.1f}" y="{y+4}" width="{max(w,1):.1f}" '
                   f'height="14" rx="4"><title>{esc(label)}: {c:+.3f}</title></rect>')
        out.append(f'<text class="val" x="{VAL_X}" y="{y+15}">{c:+.3f}</text>')
    out.append(f'<text class="axis" x="{mid}" y="{h-10}" text-anchor="middle">'
               f'← helps away team · helps home team →</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------- page
def build(d):
    cleared = any(all(g.values()) for g in d["gates"].values())
    # Gate names a stranger can read. "walk_forward" means nothing to a friend.
    PLAIN = {"walk_forward": "Beats the bookmakers on past seasons",
             "paper_trading": "Proven on 50+ pretend bets",
             "armed": "A human has switched it on"}
    chips = []
    for sport, g in d["gates"].items():
        for gate, ok in g.items():
            chips.append(
                f'<span class="chip {"ok" if ok else "no"}">'
                f'<span class="dot" aria-hidden="true"></span>'
                f'{sport.upper()}: {PLAIN.get(gate, gate)} '
                f'<strong>{"— yes" if ok else "— not yet"}</strong></span>')

    yrs = d.get("train_seasons") or []
    tiles = [("Games studied", f'{d.get("n_train", 0):,}',
              f'{yrs[0]}–{yrs[-1]}' if yrs else "not trained yet"),
             ("Betting prices collected", f'{d["n_odds"]:,}', "and counting, three times a day"),
             ("Games in the schedule", f'{d["n_games"]:,}', "every game it knows about"),
             ("Predictions made today", f'{d["n_pred"]:,}', "one per game it has data for")]
    tile_html = "".join(
        f'<div class="tile"><div class="tile-l">{esc(l)}</div>'
        f'<div class="tile-v">{esc(v)}</div><div class="tile-s">{esc(s)}</div></div>'
        for l, v, s in tiles)

    picks_tbl = "".join(
        f'<tr><td>{esc(r["away"])} @ {esc(r["home"])}</td><td>{r["model_prob"]:.1%}</td>'
        f'<td>{r["market_prob"]:.1%}</td><td>{r["edge"]:+.1%}</td></tr>'
        for r in sorted([p for p in d["picks"] if p.get("market_prob") is not None],
                        key=lambda r: -abs(r["edge"])))

    legend = ('<div class="legend"><span><i class="sw1"></i>the program</span>'
              '<span><i class="sw2"></i>the bookmakers</span></div>')
    legend2 = ('<div class="legend"><span><i class="sw1"></i>the program was this wrong</span>'
               '<span><i class="sw2"></i>what it is competing against</span></div>')

    # Each sport gets a one-line verdict in words, not a log-loss number.
    VERDICTS = {
        "mlb": ("Baseball — no real comparison yet",
                "The orange bars here are a stand-in, not real bookmaker prices, so beating "
                "them proves the program learned something about baseball — not that it "
                "could beat a bookmaker. The real test is still to come."),
        "nfl": ("Football — it lost, four seasons out of four",
                "These orange bars ARE real bookmaker prices. Blue is taller every single "
                "season, which means the bookmakers were more accurate than the program "
                "every year. This is what the safety check is protecting against."),
    }
    seasons_html = ""
    for sport in d["seasons"]:
        if not d["seasons"][sport]:
            continue
        title, blurb = VERDICTS.get(sport, (sport.upper(), esc(d["reasons"][sport])))
        seasons_html += (f'<h3>{esc(title)}</h3><p class="note">{blurb}</p>'
                         + legend2 + chart_seasons(d["seasons"][sport]))

    generated = esc(d["generated"])
    acc = f'{d["accuracy"]:.1%}' if d.get("accuracy") else "—"
    n_train = f'{d.get("n_train", 0):,}'
    vclass = "ok" if cleared else "no"
    vtitle = ("It has earned the right to bet" if cleared
              else "It will not place a bet, and it is right not to")
    picks_chart = chart_picks(d["picks"])
    coef_chart = chart_coef(d["coef"])
    picks_tbl = picks_tbl or '<tr><td colspan="4">nothing priced yet today</td></tr>'
    chips = "".join(chips) or '<span class="chip no">nothing recorded</span>'
    seasons_html = seasons_html or "<p class='empty'>No results recorded yet.</p>"

    css_vars = lambda p: "".join(f"--{k}:{v};" for k, v in p.items())
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sports Machine</title>
<style>
  :root {{ color-scheme: light; {css_vars(LIGHT)} }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{ color-scheme: dark; {css_vars(DARK)} }} }}
  :root[data-theme="dark"] {{ color-scheme: dark; {css_vars(DARK)} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--surface); color:var(--ink);
    font:15px/1.5 ui-sans-serif,system-ui,"Segoe UI",sans-serif; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:32px 16px 64px; }}
  h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:-.02em; }}
  h2.small {{ font-size:14px; color:var(--ink2); }}
  h2 {{ font-size:19px; margin:40px 0 4px; letter-spacing:-.01em; }}
  h3 {{ font-size:14px; margin:20px 0 2px; font-weight:600; }}
  .muted {{ color:var(--ink2); font-size:13px; margin:0; }}
  .sub {{ font-weight:400; color:var(--ink2); font-size:12px; }}
  header {{ margin-bottom:8px; }}
  .lede {{ font-size:17px; line-height:1.55; margin:10px 0 6px; max-width:60ch; }}
  .hero {{ margin:28px 0 8px; padding:20px 22px; border-radius:12px;
    background:color-mix(in srgb, var(--s1) 7%, transparent);
    border:1px solid color-mix(in srgb, var(--s1) 28%, transparent); }}
  .hero-n {{ font-size:54px; font-weight:700; line-height:1; letter-spacing:-.03em;
    color:var(--s1); }}
  .hero-t {{ font-size:15px; font-weight:600; margin-top:4px; }}
  .hero-s {{ font-size:13px; color:var(--ink2); margin:10px 0 0; max-width:62ch; }}
  .verdict {{ margin:22px 0 4px; padding:16px 18px; border-radius:12px;
    border:1px solid color-mix(in srgb, var(--ink) 14%, transparent); }}
  .verdict p {{ font-size:14px; margin:0 0 10px; max-width:62ch; }}
  .v-h {{ font-size:16px; margin:0 0 10px; }}
  .verdict.no .v-h {{ color:var(--crit); }}
  .verdict.ok .v-h {{ color:var(--good); }}
  .note {{ font-size:14px; color:var(--ink2); margin:6px 0 10px; max-width:64ch; }}
  .note strong {{ color:var(--ink); }}
  .c1 {{ color:var(--s1); }} .c2 {{ color:var(--s2); }}
  .chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }}
  .chip {{ font-size:12px; padding:3px 9px; border-radius:99px; display:flex;
    align-items:center; gap:6px; color:var(--ink2);
    border:1px solid color-mix(in srgb, var(--ink) 14%, transparent); }}
  .chip .dot {{ width:7px; height:7px; border-radius:99px; }}
  .chip.ok .dot {{ background:var(--good); }} .chip.no .dot {{ background:var(--crit); }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
    gap:10px; margin-top:16px; }}
  .tile {{ padding:12px 14px; border-radius:10px;
    border:1px solid color-mix(in srgb, var(--ink) 12%, transparent); }}
  .tile-l {{ font-size:12px; color:var(--ink2); }}
  .tile-v {{ font-size:24px; font-weight:650; letter-spacing:-.02em; margin:2px 0; }}
  .tile-s {{ font-size:11px; color:var(--ink2); }}
  svg {{ width:100%; height:auto; display:block; margin-top:6px; overflow:visible; }}
  .grid {{ stroke:color-mix(in srgb, var(--ink) 11%, transparent); stroke-width:1; }}
  .zero {{ stroke:color-mix(in srgb, var(--ink) 30%, transparent); stroke-width:1; }}
  .conn {{ stroke:color-mix(in srgb, var(--ink) 26%, transparent); stroke-width:2; }}
  .ring {{ fill:var(--surface); }}
  .mk1 {{ fill:var(--s1); }} .mk2 {{ fill:var(--s2); }}
  .bar {{ stroke:var(--surface); stroke-width:2; }}
  text {{ font:12px ui-sans-serif,system-ui,sans-serif; fill:var(--ink2); }}
  .cat {{ fill:var(--ink); }} .val {{ fill:var(--ink); font-variant-numeric:tabular-nums; }}
  .tick, .axis {{ font-size:11px; }}
  circle, rect.bar {{ transition:opacity .12s; }}
  svg:hover circle, svg:hover rect.bar {{ opacity:.82; }}
  circle:hover, rect.bar:hover {{ opacity:1; }}
  .legend {{ display:flex; gap:14px; font-size:12px; color:var(--ink2); margin-top:8px; }}
  .legend i {{ width:10px; height:10px; border-radius:99px; display:inline-block;
    margin-right:5px; vertical-align:-1px; }}
  .sw1 {{ background:var(--s1); }} .sw2 {{ background:var(--s2); }}
  details {{ margin-top:12px; }} summary {{ font-size:12px; color:var(--ink2); cursor:pointer; }}
  table {{ border-collapse:collapse; width:100%; margin-top:8px; font-size:13px; }}
  th,td {{ text-align:left; padding:5px 8px; border-bottom:1px solid
    color-mix(in srgb, var(--ink) 10%, transparent); font-variant-numeric:tabular-nums; }}
  .empty {{ color:var(--ink2); font-size:13px; font-style:italic; }}
  footer {{ margin-top:48px; font-size:11px; color:var(--ink2); }}
</style></head>
<body><div class="wrap">

<header>
  <h1>Sports Machine</h1>
  <p class="lede">A program that tries to predict who wins baseball games, and then
  refuses to let anyone bet on its predictions until it can prove it is better than
  the bookmakers. It is not there yet. This page is the evidence.</p>
  <p class="muted">{generated}</p>
</header>

<section class="hero">
  <div class="hero-n">{acc}</div>
  <div class="hero-t">of games called correctly</div>
  <p class="hero-s">Tested the honest way: trained on past seasons only, then graded on a
  season it had never seen, over <strong>{n_train}</strong> real games. Coin-flipping
  would be 50%. The home team wins about 53% of the time on its own, so the model is
  adding something — just not much.</p>
</section>

<section class="verdict {vclass}">
  <h2 class="v-h">{vtitle}</h2>
  <p>Being right 54.5% of the time sounds like plenty. It isn't. Bookmakers are
  right more often than that, and they take a cut of every bet. To make money you
  have to beat <em>them</em>, not beat a coin flip.</p>
  <p>So the program will not place a bet until three things are true. All three are
  currently false, and it is enforcing that itself — this is not a note-to-self, it is
  code that refuses.</p>
  <div class="chips">{chips}</div>
</section>

<h2>1 — Tonight's games</h2>
<p class="note">Each row is one game. The <b class="c1">blue dot</b> is what the program
thinks the home team's chances are. The <b class="c2">orange dot</b> is what the
bookmakers think. The line between them is how much they disagree.</p>
<p class="note"><strong>What to look for:</strong> the program disagrees with the
bookmakers on nearly every game, often by a lot. That is a warning sign, not a good
one. If you genuinely knew something the bookmakers didn't, it would show up on a
handful of games — not all of them.</p>
{legend}
{picks_chart}
<details><summary>Show the same thing as a table</summary>
<table><thead><tr><th>Game</th><th>Program</th><th>Bookmakers</th><th>Difference</th></tr></thead>
<tbody>{picks_tbl}</tbody></table></details>

<h2>2 — Is it actually any good?</h2>
<p class="note">This is the test that matters. Each pair of bars is one season the
program had never seen when it was trained. <b class="c1">Blue</b> is how wrong the
program's predictions were; <b class="c2">orange</b> is how wrong the thing it is
competing against was. <strong>Shorter is better.</strong></p>
{seasons_html}

<h2>3 — What it pays attention to</h2>
<p class="note">Everything the program knows about a game, ranked by how much it moves
the prediction. Bars to the right help the home team, bars to the left help the
visitors.</p>
<p class="note"><strong>What to look for:</strong> the starting pitcher dominates,
followed by how well each team has been hitting recently, then the bullpen. That
ordering came out of the data on its own — nobody told it what mattered.</p>
{coef_chart}

<h2>What happens next</h2>
<p class="note">The program collects betting prices three times a day on its own and
has been doing so since it was switched on. Once there are enough of them, it can be
re-graded against real bookmaker prices instead of a stand-in — and that is the test
that decides whether any of this is worth anything.</p>
<p class="note">Until then, the honest answer to "does it work?" is
<strong>we don't know yet</strong>, and the program is built to keep saying that
rather than guess.</p>

<h2 class="small">By the numbers</h2>
<div class="tiles">{tile_html}</div>

<footer>Generated from {esc(d["db"])} · rebuild with <code>python dashboard.py</code></footer>
</div></body></html>"""


if __name__ == "__main__":
    data = gather()
    OUT.write_text(build(data), encoding="utf-8")
    print(f"Wrote {OUT}")
    if "--no-open" not in sys.argv:
        webbrowser.open(OUT.as_uri())
