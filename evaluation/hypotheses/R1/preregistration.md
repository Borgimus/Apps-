# R1 | Monthly sector ETF rotation

Use yfinance adjusted daily closes for SPY and the eleven sector SPDR ETFs.
Study 1999-01-01 through 2026-11-20. Sector listing boundaries and indicator
warm-up are respected. At the final trading close of each month rank ETFs
by month-end close one month earlier divided by month-end close twelve months
earlier, less one. Rank eligible sectors using descending return and alphabetical
ties. Require at least three eligible sectors. Hold the top three equal weight
from the first trading close of the next month until the following first close.
Equal weight across all ETFs with the same lookback is primary; SPY is secondary.
Apply cost each time capital is traded, using absolute changes from drifted weights
including initial buys. Monthly drawdown is based on marked monthly closes.

The primary comparison is monthly candidate minus equal-weight benchmark at
10 bps per traded leg, Newey–West lag 3. Require 180 paired complete months,
t >= 3, positive candidate net mean at 10 bps, nonnegative at 20 bps, positive
incremental sign in both halves and last 30%, and maximum single-year
contribution <= 40% of positive aggregate incremental performance.
Unresolved selected prices block promotion. No intraday quote claims.
