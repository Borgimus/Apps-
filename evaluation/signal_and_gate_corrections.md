# Signal-quality and risk-gate corrections

Status: **prepared for the next cohort, not deployed.** These changes alter
which setups are eligible and which entries the risk manager admits, so they
must not be mixed into an existing cohort. Ship them with the cohort declared
under `evaluation/alpaca_options_provider_rollout.md` (or any later cohort),
never into a running one.

## 1. Quality scores use only what was known when the signal fired

The VWAP scorer's fourth point, "next bar confirms direction", read the bar
after the signal bar. VWAP signals are stamped at their last confirmation bar,
so that bar never exists when a signal first appears. A fresh signal therefore
scored at most 3, while the same signal re-scored 5–10 minutes later (still
inside the 10-minute signal-age limit) could score 4. With a minimum of 3, the
gate favoured stale signals. Among the 64 fill-validated model-4 closes, setups
that passed the filters had a median peak gain of +3.9%, against +12.6% for
setups that failed them.

Now every score is computed on bars up to and including the signal bar. The
VWAP point that needed a later bar is removed, so VWAP scores 0–3; ORB keeps
0–4 (its fourth point, VWAP alignment, is known at the signal bar).
**Consequence:** with `paper_scaled_min_signal_quality = 3.0`, a VWAP setup must
meet all three remaining criteria. Revisit that threshold in the new cohort's
protocol rather than inheriting it.

## 2. A scoring failure is "unscored", not 0

Exceptions inside the scorers were logged at DEBUG and returned the partial
score, typically 0, which was then recorded as `signal_quality_below_min`.
Scoring failures now log a warning and return `None`. The entry gate and the
shadow eligibility record them as `signal_quality_unavailable`, a distinct
reason.

## 3. Entries reserve their stop loss against the daily limits

The daily loss limits checked realized losses only. At -$200 of a $250
experiment limit, an entry that could lose another $250 was admitted.
`app/risk/loss_capacity.py` now defines one rule, shared by the live risk
manager and the chronological replay: realized losses plus the new trade's
loss at its configured stop must fit inside each limit (account 2% of starting
equity; experiment $250). Gaps can still exceed the stop.

Replay applies the rule only when the captured policy contains
`reserve_trade_loss: true`. Policies captured before this change lack the key,
so historical sessions replay exactly as before. New sessions capture it, which
changes their replay policy hash: the expected cohort boundary.

## 4. One blocked-symbol rule

The live entry filter and shadow eligibility normalized the blocked-symbol list
differently (both happened to be case-insensitive). Both now call
`entry_filters.symbol_blocked`.
