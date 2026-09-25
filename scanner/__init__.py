"""The scanner: find prices that are wrong, prove it on paper, never trade.

Built from docs/briefs/2026-09-25-next-task.md. Phase A is the foundation:

  venues/     one tiny adapter per venue, turning its native format into the
              markets/prices rows every other module reads
  store       those two tables: market keys, inserts, "the price at a moment"
  fees        what trading at a price costs, per venue; every EV is after fees
  fair        fair_value(): the one place a fair price is computed
  budget      the Odds API credit ledger, the caps, and the polling plan
  poll        the budget-aware polling loop and its supervisor
  paper       paper orders, fills against the NEXT observed price, positions
  capital     stake, days to resolution, annualized EV, capital locked
  strategies  the registry: adding a strategy is a file
  gates       each strategy's own gate record in validation.json
  scoreboard  per strategy, per venue, and in total

Nothing here can place a real order. There is no order endpoint anywhere in
the code, paper_orders refuses any mode but paper/placebo at the database
level, and audit.py checks both.
"""
