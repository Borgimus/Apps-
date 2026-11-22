# V1 | Implied versus realized volatility

This is an informational screen. VIX reflects SPX option prices and SPY is a
related underlying proxy. Use yfinance raw VIX and VIX9D closes, and adjusted
SPY closes. Study 2000-01-01 through 2026-11-20 where both inputs exist.
On a SPY session date compare VIX at the close with annualized realized
volatility from the next 21 trading-session close-to-close log returns.
Report VIX9D against the next five trading days separately; the calendar
tenor mismatch makes that comparison diagnostic. Use sqrt(252 times the
mean squared log returns), expressed in percentage points. Drop no selected
values except explicit null horizons at the end of the sample, which are counted.
Report the mean implied-minus-realized spread, share above realized, distribution,
worst 1%, yearly splits, and Newey–West with lag 21 or 5 for overlap.

Informational support requires t >= 3, at least 100 eligible observations,
positive average spread in both halves and the last 30%, and no one year
contributing above 40% of aggregate signed spread. Equity cost thresholds do not
apply to a volatility-index measurement. Neither a pass nor a failure establishes
option returns. V1 stage 2 requires entitled timestamped OPRA data; the collector
is mock-tested only. The next stage is blocked pending the owner's data decision.
