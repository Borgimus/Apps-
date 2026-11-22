# S1 | Sector-relative reversal

Use the shared research protocol and committed sector map. A loss not shared by
the matched sector may be temporary. At each close available between 10:00 and
11:30 ET, compare the stock's open-to-close return with the corresponding
sector return times beta. Estimate beta with intercept from matched five-minute
close-to-close returns within the prior 20 market sessions, excluding overnight
returns. Use sample standard deviation of those residuals, scaled by the square
root of elapsed five-minute bars. Require at least 100 matched returns across
at least 15 of those prior sessions. Require a complete current prefix from
the open. Trigger on residual <= -1.5 scales, sector return >= -0.25%, latest
low no lower than the earlier session low, and close >= bar open.

Primary control is a similarly stabilized raw dip in the same stock on a
sector-down observation (sector return < -0.25%). The dip must be at least as
large as the median observed trigger drawdown of that stock on strictly prior
sessions within the study. A stock with no prior qualifying trigger has no
ordinary-dip threshold yet. Secondary control is the protocol's seeded matched
non-signal session. The primary comparison is S1 minus ordinary dip at
30 minutes and 10 bps. Use all shared pass and kill thresholds. Read S1 and S3
together, counting them as two tests.
