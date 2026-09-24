# Report point 3 — Phase 2, the scoreboard

`mode='manual'`: a human's bets graded on exactly the terms the model's are.
It never stakes money — `bets/engine.py` still refuses every real wager until
the three gates pass.

## The demo: three fake bets, entered, graded, then deleted

```
python run_daily.py bet --sport nfl --date 2026-10-04 \
    --game "Chiefs at Raiders" --market h2h --side "Kansas City Chiefs" \
    --price -215 --book draftkings --stake 25 --tag research
python run_daily.py bet --sport nfl --date 2026-10-04 \
    --game "Chargers at Seahawks" --market h2h --side "Seattle Seahawks" \
    --price +145 --book fanduel --stake 20 --tag boost
python run_daily.py bet --sport nfl --date 2026-10-05 \
    --game "Falcons at Saints" --market player_receptions \
    --player "Drake London" --prop-line 5.5 --side over \
    --price -120 --book betmgm --stake 10 --tag research --result win
```

All three resolved to a real stored game from a shorthand matchup string,
recorded the best price available across held books and the fair price at
entry, and were graded through the same `closing_snapshot` / `fair_prob` path
the paper bets use.

### It refuses what it should

```
REFUSED: 'Nonexistent at Nowhere' matched no nfl game on 2026-10-04.
         Stored that day: Indianapolis Colts at Washington Commanders;
         Arizona Cardinals at New York Giants; Tennessee Titans at ...
REFUSED: 50 is not a valid American price
```

It also refuses a bet on a game that has already started, an ambiguous matchup
that matches two games, a made-up tag, a zero stake, a prop market with no
player, and a spread with no number.

### The scoreboard

```
3 bet(s), 1 settled, 0 graded for CLV  (coverage 0%)

  ROI      +83.30%  [+83.30%, +83.30%]  bootstrap 95%
  EV       +15.62%   against the fair close
  shop     +15.62%   the price you got
  info          -    what you knew

by tag
  tag               n  settled      ROI       EV     shop     info
  boost             1        0        -  +78.48%  +78.48%       -
  research          2        1  +83.30%  -15.81%  -15.81%       -

by market
  market            n  settled      ROI       EV     shop     info
  h2h               2        0        -  +39.17%  +39.17%       -
  player_receptions 1        1  +83.30%  -31.49%  -31.49%       -

Is this handicapping worth continuing? Same bar as the model.
  50+ graded bets            not yet
  info clear of zero by 3 SE no
  coverage >= 75%            no (0%)

  VERDICT: NOT PROVEN
```

**The `info` column is blank, and that is the correct answer rather than a
zero** — all three demo games are in the future, so no closing price exists to
measure movement against. `shop` is implausibly large for the same reason: the
fair-at-entry price came from whatever snapshot the copied database happened to
hold for games two weeks out. On a bet placed against a live market it is a
fraction of a percent.

The fake bets were deleted afterwards; `bets` is back to its 20 real rows.

## What it records at entry, and why

A human's pick has no model snapshot, so both the **best price available
elsewhere** and the **fair price** are recorded at entry. Without them the
shop/info split cannot be computed for a human's bets, and the scoreboard could
only say "you won" — not whether you won by beating the number or by finding a
better price for a number everyone already had.

Boosted prices are graded against the same fair close, so a boost's real value
shows up as `shop` rather than as skill.

## The schema change, and the one that was avoided

Seven additive columns on `bets`: `market`, `player`, `prop_line`, `tag`,
`manual_result`, `best_available`, `p_fair_at_bet`. Applied to the **copy**
first; `python db.py` twice in a row is a no-op the second time; 20 existing
rows unchanged; golden test clean afterwards.

`prop_line` is separate from `line_taken` deliberately: `line_taken` is the
**price** (−110), `prop_line` is the **number** (5.5). Conflating them is how a
scoreboard ends up grading "over 110".

**What was deliberately not done.** `model_prob`, `novig_market_prob`, `edge`
and `kelly_fraction` are `NOT NULL`, and a manual bet has none of them.
Rebuilding `bets` to make them nullable is a destructive ALTER on the one table
that records money. Instead both probabilities are set to the **fair price at
entry**, so `edge` is exactly **0.0** — at entry a manual bet claims no edge,
because no claim was made. Whether there was one is measured afterwards, as
shop and info, which is the entire point of the exercise.

## The verdict it gives

Same bar as the model, deliberately: 50+ graded bets, `info` clear of zero by
3 SE, coverage ≥ 75%. A human's picks do not get an easier standard than the
model's, because the whole purpose is to be able to compare them.

## Tests

21, no database and no network: every entry refusal; the void rule (an
unsettled bet is not a loss, so the scoreboard cannot get worse the more bets
are pending); coverage measured over *settled* rather than over everything; the
bootstrap CI being seeded and returning NaN on empty rather than 0%; and the
`(1+shop)(1+info) = 1+ev` identity.

## Documented

`COMMANDS.md`, `docs/state-of-the-machine.md` and the integration matrix, in
the same commit. `integration_matrix.py --check` exits non-zero if a mode
exists without being documented — so this could not have been skipped.
