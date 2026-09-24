# Cleanup findings — things that look wrong, left alone

Phase 1's ground rule: **no logic edits**. Anything that looks wrong is written
here with file:line and not touched. Each entry says what it is, why it was not
changed, and what changing it would take.

---

## 1. `backfill.py:46` bypasses the status mapping — the one with teeth

```python
if g["status"]["abstractGameState"].lower() != "final":
    continue
```

`feeds.stats_api_status()` exists precisely because **`abstractGameState` is
`"Final"` for a postponed game as well as a played one**. That is not a guess:
it is the bug that put 88 postponed games into the training set in their
original slot, found during the Phase 5 ten-request test, and `stats_api_status`
reads `detailedState` to separate them.

This line reads the raw field and so cannot make that distinction. Every other
ingest path goes through the helper.

**Not changed, because changing it is a logic edit**, and the ground rule for
this phase is explicit. It is also not obviously a live defect: the upsert this
feeds has `status='final'` as terminal and there are downstream guards, so the
damage may already be caught. Deciding that needs a measurement — count the
games this path admits whose `detailedState` is not `Final` — not a one-line
change made during a cleanup.

**To fix properly:** call `stats_api_status(g["status"])` and compare to
`"final"`, then re-derive how many rows in `games` change status, then a test
that a postponed payload is rejected here specifically.

---

## 2. Vectorised de-vig, twice, outside `bets/engine.py`

- `research/market_panel.py:69` — `return pa / tot, ph / tot, tot - 1.0`
- `research/b3/materialize.py:106` — `wide["p_over_book"] = pa / total`

Both compute the same proportional de-vig as `bets.engine.novig_probs`, over
**arrays** rather than a scalar pair. Collapsing them into the canonical
function would mean either calling it per row (slow enough to matter on 169,510
prop rows) or rewriting the canonical one to be array-aware — a rewrite, not a
move.

**Not changed.** Both are research code, neither is on a path that stakes
money, and both already carry a comment pointing at the canonical version. The
right fix is a vectorised sibling in `bets/engine.py` that the scalar one
delegates to, which is a small design change and belongs in its own commit.

---

## 3. "Pinnacle else consensus" chosen in three places

- `bets/log.py:150` `fair_prob` — the canonical one
- `research/market_panel.py:165`
- `research/b3/materialize.py:116`

All three implement the same rule and all three record which source they used,
which is the part that matters. The two copies are pandas expressions over a
frame; the canonical is a per-snapshot SQL lookup. Same rule, different shape.

**Not changed**, same reason as 2.

---

## 4. Two modules the brief suspected of being dead are not

- **`export_snapshots.py` is live.** It is invoked by
  `.github/workflows/daily.yml:79` and is in `audit.py`'s bare-import check.
  Zero Python importers, because it is an *entry point*, not a library. Kept.
- **`resolve_market_close.py` is a manual entry point.** No caller by design —
  it materialises `market_close` and is run by hand after a backfill. Kept.

Only `features/registry.py` was genuinely dead (no importers, and its own
docstring said "Nothing imports this yet"). Deleted.

---

## 5. `predictions` is append-only and some consumers still count rows

Carried forward from the brief's own list, confirmed:
`dashboard.py` counts rows in `predictions` as though one row were one game.
Since the table became append-only, a game scored twice in a day has two rows,
so the count is reruns and not games.

**This one IS fixed**, in 1.6, because the brief names the dashboard's facts as
the deliberate exception to behaviour-preservation. Recorded here so the two
places agree.

---

## 6. Unused imports

26 across 17 files, listed in `docs/cleanup-inventory.md`. Removed where the
name is genuinely unreferenced. Three were left: `audit.py` imports modules
inside functions to prove they import cleanly, which looks unused to a parser
and is the entire point of the check.
