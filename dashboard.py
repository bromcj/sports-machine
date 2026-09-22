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
    """Grouped bars, model vs baseline log-loss. One scale - never two axes."""
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
           f'aria-label="Model versus baseline log-loss by test season">']
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
                       f'{s["season"]} {name} {v:.5f}</title></rect>')
        out.append(f'<text class="cat" x="{cx:.1f}" y="{h-PAD_B+20}" text-anchor="middle">{s["season"]}</text>')
    out.append(f'<text class="axis" x="{PAD_L-10}" y="{PAD_T-6}" text-anchor="end">log-loss</text>')
    out.append("</svg>")
    return "".join(out)


def chart_coef(coef):
    """Diverging bars around zero. One series, so no legend - the title names it."""
    if not coef:
        return "<p class='empty'>No trained model yet.</p>"
    ROW, PAD_T = 26, 14
    h = PAD_T + len(coef) * ROW + 30
    # Values live in a fixed right-hand column, not at the bar ends. Anchoring
    # them to the end of a NEGATIVE bar walks them left into the feature names -
    # at full width the longest bar collided with its own label.
    LBL_R, mid, half, VAL_X = 196, 430, 196, 646
    mx = max(abs(c) for _, c in coef) or 1
    out = [f'<svg viewBox="0 0 740 {h}" role="img" '
           f'aria-label="Ridge coefficient for each feature, signed">']
    out.append(f'<line class="zero" x1="{mid}" y1="{PAD_T-4}" x2="{mid}" y2="{h-30}"/>')
    for i, (name, c) in enumerate(coef):
        y = PAD_T + i * ROW
        w = abs(c) / mx * half
        x = mid if c >= 0 else mid - w
        cls = "mk1" if c >= 0 else "mk2"
        out.append(f'<text class="cat" x="{LBL_R}" y="{y+15}" text-anchor="end">{esc(name)}</text>')
        out.append(f'<rect class="{cls} bar" x="{x:.1f}" y="{y+4}" width="{max(w,1):.1f}" '
                   f'height="14" rx="4"><title>{esc(name)} {c:+.4f}</title></rect>')
        out.append(f'<text class="val" x="{VAL_X}" y="{y+15}">{c:+.3f}</text>')
    out.append(f'<text class="axis" x="{mid}" y="{h-10}" text-anchor="middle">'
               f'← helps away team · helps home team →</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------- page
def build(d):
    cleared = any(all(g.values()) for g in d["gates"].values())
    chips = []
    for sport, g in d["gates"].items():
        for gate, ok in g.items():
            chips.append(
                f'<span class="chip {"ok" if ok else "no"}">'
                f'<span class="dot" aria-hidden="true"></span>'
                f'{sport.upper()} · {gate.replace("_", " ")}: '
                f'<strong>{"pass" if ok else "not yet"}</strong></span>')

    tiles = [("Games in database", f'{d["n_games"]:,}', "every schedule row collected"),
             ("Odds snapshots", f'{d["n_odds"]:,}', f'{d["n_close"]:,} tagged close'),
             ("Games priced today", f'{d["n_pred"]:,}', "model has an opinion"),
             ("Training games", f'{d.get("n_train", 0):,}',
              f'seasons {d.get("train_seasons", [""])[0]}–{d.get("train_seasons", ["", ""])[-1]}'
              if d.get("train_seasons") else "not trained")]
    tile_html = "".join(
        f'<div class="tile"><div class="tile-l">{esc(l)}</div>'
        f'<div class="tile-v">{esc(v)}</div><div class="tile-s">{esc(s)}</div></div>'
        for l, v, s in tiles)

    picks_tbl = "".join(
        f'<tr><td>{esc(r["away"])} @ {esc(r["home"])}</td><td>{r["model_prob"]:.1%}</td>'
        f'<td>{r["market_prob"]:.1%}</td><td>{r["edge"]:+.1%}</td></tr>'
        for r in sorted([p for p in d["picks"] if p.get("market_prob") is not None],
                        key=lambda r: -abs(r["edge"])))

    legend = ('<div class="legend">'
              '<span><i class="sw1"></i>model</span>'
              '<span><i class="sw2"></i>market</span></div>')
    legend2 = ('<div class="legend">'
               '<span><i class="sw1"></i>model</span>'
               '<span><i class="sw2"></i>baseline</span></div>')

    seasons_html = "".join(
        f'<h3>{sport.upper()} <span class="sub">{esc(d["reasons"][sport])}</span></h3>'
        + legend2 + chart_seasons(d["seasons"][sport])
        for sport in d["seasons"] if d["seasons"][sport])

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
  h2 {{ font-size:17px; margin:40px 0 4px; letter-spacing:-.01em; }}
  h3 {{ font-size:14px; margin:20px 0 2px; font-weight:600; }}
  .muted {{ color:var(--ink2); font-size:13px; margin:0; }}
  .sub {{ font-weight:400; color:var(--ink2); font-size:12px; }}
  .verdict {{ margin:20px 0 4px; padding:14px 16px; border-radius:10px;
    border:1px solid color-mix(in srgb, var(--ink) 14%, transparent); }}
  .verdict b {{ color:{'var(--good)' if cleared else 'var(--crit)'}; }}
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

<h1>Sports Machine</h1>
<p class="muted">{esc(d["generated"])}</p>

<div class="verdict">
  <b>{"CLEARED — a sport may stake money" if cleared else "NOT CLEARED — no sport may stake money"}</b><br>
  <span class="muted">Every gate must pass before the bet engine will size a wager.
  Re-running a walk-forward or recording negative CLV closes them again.</span>
  <div class="chips">{"".join(chips) or '<span class="chip no">nothing recorded</span>'}</div>
</div>

<div class="tiles">{tile_html}</div>

<h2>Today — model against the market</h2>
<p class="muted">Each row is one game. The gap is how far the model disagrees with the price.
A wide gap is <em>disagreement</em>, which only becomes edge once the model is proven
better than the market.</p>
{legend}
{chart_picks(d["picks"])}
<details><summary>Show as a table</summary>
<table><thead><tr><th>Game</th><th>Model</th><th>Market</th><th>Gap</th></tr></thead>
<tbody>{picks_tbl or '<tr><td colspan="4">nothing priced</td></tr>'}</tbody></table></details>

<h2>Walk-forward — is the model better than the baseline?</h2>
<p class="muted">Lower is better. Both bars share one scale.</p>
{seasons_html or "<p class='empty'>No walk-forward recorded.</p>"}

<h2>What the model actually leans on</h2>
<p class="muted">Standardised ridge coefficients — the effect of each feature on projected
run differential, holding the others fixed.</p>
{chart_coef(d["coef"])}

<footer>Generated from {esc(d["db"])} · rebuild with <code>python dashboard.py</code></footer>
</div></body></html>"""


if __name__ == "__main__":
    data = gather()
    OUT.write_text(build(data), encoding="utf-8")
    print(f"Wrote {OUT}")
    if "--no-open" not in sys.argv:
        webbrowser.open(OUT.as_uri())
