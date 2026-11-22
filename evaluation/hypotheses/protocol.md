# Strategy study protocol 1

Declared November 22, 2026 before candidate results. The source handoff is
evaluation/strategy_research_program_handoff.md on agent/strategy-research-program-handoff.
The operational baseline and portfolio policy are independently fingerprinted.
All outputs are research proxies. This program has no execution or arming path.

## Intraday primary design

Study 2016-01-01 through 2026-11-20 inclusive where 5-minute history is available.
Require at least five calendar years of actual SPY coverage plus at least 100
candidate days for a conclusion. Request SIP if entitled, otherwise IEX. No
mixed feeds. Use the fixed handoff sector map, flag QQQ's weak fit and report the
panel's survivorship exposure. Use completed regular-session bars in the
09:30–12:30 ET window and no decision after 11:30 ET. Historical bars are
available 60 seconds after close. A live archive uses its actual observed_at.
Decision waits for the last required input. The entry is the first bar open
whose start is at or after that decision. Compute 30m, 60m and 12:30 exits.
Any missing holding bar gives a null outcome. Evaluate long entries only.
First occurrence per symbol and day in each arm. When multiple trades occur
on one day, average them before comparing. Round-trip costs 5/10/20 bps per leg.

Primary incremental statistic at 30 minutes is the difference of average
10 bps net daily means. The S3 control is paired on each signal. S1 and S2 use
unpaired daily arms and day-clustered influence scores allowing overlap; no
trade days are not outcome observations. Newey–West lag 6 on ordered active
day clusters and a circular six-day block bootstrap are reported. Require
t >= 3, positive candidate mean at 10 bps, nonnegative candidate mean at
20 bps, positive incremental sign in both chronological halves and the
last 30%, 100 independent candidate days, and largest positive year's
contribution <= 40% of the net incremental total. The chronological splits
are determined from the data calendar and are not tuned. An unresolved
primary outcome blocks promotion. Other horizons and subgroup results are
diagnostic. No grid or alternative rule selects a pass.

Use a deterministic alphabetic tie-break after the frozen universe order for
any extra ticker. Primary unconstrained daily signal means are evaluated
separately from standalone and shared-slot portfolio proxies. Load the frozen
policy at runtime. Benchmark controls have the same cost and holding assumptions.
For S1 and S2 timing diagnostics, match the same symbol and decision clock
on a non-signal session in the same year, with seeded pre-outcome random
choice and without reusing a control date for that symbol. Pool 2016–2026
family reports only as exploratory comparisons, never as independent tests.

## Archive and forward

A historical survivor must pass archive provenance, have completed outcomes,
and retain the sign and at least 50% of the historical primary incremental
effect. Archived days are a feasibility check. The forward test starts after
registration and needs both 60 new full valid sessions and 100 candidate
signals before a decision. Use the same preregistered hypothesis and economics.
Evaluate forward criteria at the predeclared endpoint without optional
stopping. Operational deployment, additional data spending and any paper
decision are owner decisions.
