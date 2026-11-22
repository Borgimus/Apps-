# S3 | Sector-confirmed relative strength

Use the committed sector map. Demand may persist when an individual stock
outperforms an already rising sector. Once both symbols' 09:55–10:00 closes
and their 09:30 opens are available, compute stock return minus sector return.
Trigger a long when excess >= 0.5% and sector return > 0. Use actual delivery
time and next eligible bar open. Primary control buys that sector ETF at the
same time. Use the same symmetry and costs for each candidate/ETF pair.
Secondary diagnostic tests excess >= 0.5% on sector-down sessions.

The paired incremental 30-minute difference net of 10 bps is primary. Use all
shared pass and kill thresholds. Compare with S1 as two registered tests of
relative movement with opposite mechanisms. S3b, including the overnight
gap, is deferred as a separate hypothesis.
