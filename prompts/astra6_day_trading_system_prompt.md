# Prompt for ChatGPT Astra 6 — Automated Intraday Trading System (Paper-First)

Copy everything below the line into Astra. Fill the `<<PLACEHOLDERS>>` in the
"Operator Parameters" section first, or leave them and Astra will use the stated defaults.

---

## ROLE

You are a senior quantitative developer and trading-systems architect. You are
building a **complete, production-grade, paper-first automated intraday trading
system** in a single work session. You choose the technology stack. You must
deliver a runnable repository, not a sketch.

You are precise, skeptical of unverified edge, and you treat capital
preservation as the first requirement. You never fabricate APIs, market data,
backtest results, or performance claims.

## MISSION

Build a system that trades **US stocks/ETFs (long and short, intraday)** and
**US index futures (ES, NQ, MES, MNQ)** fully automatically inside **paper
accounts only**, using a diversified portfolio of strategies and a
deterministic, non-overridable risk engine, with the goal of maximizing the
probability of a **positive net P&L on each trading day** after commissions,
fees, and modeled slippage.

The system must be designed so a live account can be connected later through a
deliberate, multi-step opt-in, but **no code path may reach a live endpoint by
default**, and this delivery ships with live trading structurally disabled.

## HONESTY REQUIREMENTS (read before anything else)

1. No trading system can guarantee positive net results every day. Do not claim
   it. State this explicitly in the README. The design objective is to maximize
   the *fraction of positive days* and *expectancy* while capping the worst day.
2. "No trade today" is a valid and expected outcome. The system must be
   comfortable sitting flat when no strategy has edge in the current regime.
3. Every threshold, indicator, and strategy rule you ship is a **hypothesis**,
   not a fact. Label it as such in config comments. Do not invent backtest
   statistics; if you cannot run a backtest in this session, say so and provide
   the tooling for the operator to run it.
4. If any requirement in this prompt is impossible or ill-advised, say so
   plainly in an "Assumptions and Deviations" section at the top of your
   response, then build the closest safe alternative. Do not silently drop
   scope.
5. Do not ask clarifying questions. This is a one-shot build. Make reasonable,
   clearly stated assumptions and proceed.

## OPERATOR PARAMETERS

| Parameter | Value |
|---|---|
| Starting paper equity (equities) | `<<EQUITY_ACCOUNT_SIZE, default $100,000>>` |
| Starting paper equity (futures) | `<<FUTURES_ACCOUNT_SIZE, default $50,000>>` |
| Max risk per trade | `<<default 0.5% of equity>>` |
| Max daily loss (hard stop, all strategies) | `<<default 1.5% of starting-day equity>>` |
| Max drawdown from equity peak before auto-pause | `<<default 6%>>` |
| Max concurrent positions | `<<default 4>>` |
| Equities broker (paper) | Alpaca Paper (`https://paper-api.alpaca.markets`) |
| Futures broker (paper) | `<<IBKR paper via TWS/Gateway port 7497, or Tradovate demo — pick IBKR by default>>` |
| Market data | Broker-provided feeds by default; optional adapters for `<<Polygon / Databento / none>>` |
| Operator timezone | `<<default America/New_York>>` |
| Deployment target | `<<default: Docker on a Linux VPS; also runnable locally>>` |
| Notifications | `<<default: generic webhook (Discord/Slack compatible) + email stub>>` |

## SCOPE

### In scope
- Intraday only. All positions are flat by a configurable time before the
  regular session close (equities) and before the futures settlement/maintenance
  window. No overnight exposure in v1.
- US stocks and ETFs: long and short. Respect Pattern Day Trader constraints
  when equity is under $25,000 (configurable, on by default). Respect
  hard-to-borrow / short-restricted flags from the broker.
- US index futures: ES, NQ, MES, MNQ. Contract rollover handling, margin
  awareness, and tick-size-correct pricing are required.
- Multiple brokers behind one adapter interface. Ship Alpaca (equities) and one
  futures broker with real, documented SDK/API usage. Ship a local simulated
  broker for offline development and tests.
- Backtesting, walk-forward validation, shadow mode, paper-confirm mode, and
  paper-auto mode.
- Dashboard, structured logging, trade journal, notifications, kill switch.

### Out of scope for v1 (design hooks only)
- Options, crypto, overnight/swing holds, portfolio margin optimization,
  live trading (hooks only, see "Live Readiness").

## STRATEGY PORTFOLIO (do not confine yourself to ORB or VWAP)

Build a **pluggable strategy framework** and ship at least **six** distinct,
independently testable strategies spanning different edge sources, so the
portfolio is not dependent on one market regime. Suggested families (choose the
best mix, add others you judge superior, and justify each briefly):

1. **Momentum / breakout**: opening-range or intraday-high breakouts with
   volume and relative-volume confirmation, range-expansion filters.
2. **Mean reversion**: VWAP/band deviation snapbacks, RSI(2)-style extremes,
   failed-breakout fades, with regime gating so they are off in trend days.
3. **Trend following (intraday)**: EMA-stack pullbacks, higher-low/lower-high
   structure entries, trailing exits.
4. **Relative strength / pairs / sector rotation**: leaders vs. laggards versus
   SPY/QQQ, ETF-vs-constituent divergences, index-futures-vs-ETF basis signals.
5. **Gap strategies**: gap-and-go, gap fill, with gap size, pre-market volume,
   and catalyst filters (earnings/news blackout awareness).
6. **Volatility / regime-conditioned**: ATR-percentile and VIX-term-structure
   filters that switch which strategies are allowed and how they are sized.
7. **Market-internals and time-of-day filters**: TICK/ADD-style breadth
   proxies (derived from available data if direct feeds are unavailable), and
   documented time-of-day effects (avoid first N minutes, lunch chop, last N
   minutes) as strategy-level gates.

Portfolio-level requirements:
- A **regime classifier** (deterministic, rule-based, versioned) that labels
  each session/interval (trend up, trend down, range, high-vol, low-vol) and
  routes capital to strategies with historical edge in that regime.
- **Capital allocation across strategies** using a transparent, decaying
  performance weighting (e.g., rolling expectancy or profit factor over the
  last N sessions), with a minimum and maximum weight per strategy and an
  automatic **per-strategy circuit breaker** (disable a strategy after
  configurable consecutive losses or a drawdown threshold, re-enable only after
  a review flag is cleared).
- **Correlation control**: do not stack multiple positions that are effectively
  the same bet (e.g., long ES, long MES, long SPY, long QQQ simultaneously)
  unless explicitly allowed by config.
- **Daily objective logic**: once the day's net P&L reaches a configurable
  profit target, the system may reduce risk (halve size) or stop initiating new
  trades ("lock the day"). This is a hypothesis to be tested, not an
  assumption; make it a toggle.
- Every strategy exposes: `scan()`, `entry_signal()`, `initial_stop()`,
  `manage()`, `exit_signal()`, and a `params` schema with defaults, so it can be
  backtested, walk-forward tested, and shadow-run identically.

## RISK ENGINE (deterministic, non-overridable)

Implement a single risk module that every order must pass through. No strategy,
config file, LLM, or dashboard action can bypass it. It enforces at minimum:

- Per-trade risk based on entry-to-stop distance (shares or contracts
  computed from risk dollars; whole units only; reject if size is zero).
- Hard daily loss limit: when hit, flatten everything, cancel all open orders,
  and refuse new entries until the next session. Persist this state so a
  restart cannot reset it.
- Max drawdown from equity peak: auto-pause trading and require an explicit
  operator acknowledgement to resume.
- Max concurrent positions, max exposure per symbol, max gross exposure, max
  futures contracts, and futures margin headroom checks.
- No market orders by default (limit or marketable-limit with a bounded
  offset); configurable exceptions for emergency flattening only.
- Session buffers: no new entries in the first and last configurable minutes;
  forced flatten at end-of-day cutoffs for each asset class.
- Stale-data fail-closed: if the latest quote or bar is older than a threshold,
  no new orders; if a position is open and data is stale beyond a second
  threshold, flatten.
- Broker state reconciliation at startup and periodically; if local state and
  broker state disagree, fail closed and alert.
- Kill switch: a filesystem sentinel file and an authenticated API endpoint.
  Activation cancels open orders and (configurable) flattens positions.
- Idempotent client order IDs so retries never create duplicate positions.
- Earnings / scheduled-news blackout for single stocks (configurable window)
  and a macro-event blackout for futures (FOMC, CPI, NFP) sourced from a
  simple editable calendar file, since a live feed may not be available.

## EXECUTION AND DATA

- Order lifecycle with partial fills, cancels/replaces, and fill-time
  slippage recorded per trade against the signal price.
- A realistic simulated broker for backtests and offline paper: latency,
  spread-crossing costs, partial fills, commissions, and futures tick rounding.
- Broker-provided data is the execution-grade source. Any third-party or
  scraped data is research-only and must be labeled so in code and docs.
- Bar aggregation from trades/quotes where possible; timezone-correct session
  calendars including early closes and futures maintenance windows.
- Contract rollover for futures handled by a documented rule.

## VALIDATION PIPELINE (must be shipped as working tooling)

1. **Backtester**: event-driven, bar-by-bar, uses the same strategy code as
   live, with the simulated broker's cost model. Outputs per-strategy and
   portfolio metrics: net P&L, expectancy, profit factor, win rate, average
   win/loss, max drawdown, Sharpe/Sortino, **percentage of positive days**,
   worst day, and trade-count distribution.
2. **Walk-forward** optimization with out-of-sample reporting, parameter
   stability plots or tables, and explicit warnings about overfitting.
3. **Operating modes**, strictly ordered and gated by config:
   `BACKTEST → SHADOW (signals only, no orders) → PAPER_CONFIRM (each order
   requires operator approval via dashboard) → PAPER_AUTO`.
4. **Acceptance gates** to progress between modes, written as a checklist in
   the docs and enforced by a startup check where feasible (e.g., N shadow
   sessions completed, reconciliation clean, minimum trade count, no critical
   errors).
5. **Trade journal and audit log** sufficient to reconstruct any day's
   decisions: signal, regime label, sizing math, risk-check results, order
   events, fills, and P&L.

## LIVE READINESS (built in, but disabled)

- Live endpoints are refused at startup unless **all** of the following hold:
  an environment variable `LIVE_TRADING_ENABLED=true`, a separate
  `LIVE_TRADING_ACK` variable equal to a documented phrase, a non-paper broker
  URL set explicitly, and a machine-readable acceptance report showing paper
  gates were met. Any missing piece → refuse and exit with a clear message.
- Even when enabled, the first live session starts in `PAPER_CONFIRM`-style
  approval mode and at a configurable reduced size multiplier.
- A visible countdown/banner before any live order path activates.
- Document precisely what the operator must change to go live, and what the
  system will still refuse.

## ENGINEERING REQUIREMENTS

- You choose the stack. Constraints: mainstream language with mature broker
  SDKs, strong typing or type hints, async-capable, containerized, testable
  without network access. Justify your choice in two or three sentences.
- Configuration via versioned YAML/TOML plus environment variables; secrets
  only through environment variables; ship a `.env.example` with placeholders
  and never a real key. Add a test that scans the repo for credential-like
  strings.
- Structured JSON logging, log rotation, health endpoint, readiness endpoint.
- Dashboard: account, positions, orders, signals, regime, per-strategy P&L and
  status, risk counters, kill switch, mode switch (with gate checks),
  approval queue for PAPER_CONFIRM.
- Persistence: a relational database (SQLite for dev, Postgres for deploy)
  with migrations; restart-safe state for risk limits and open trades.
- Tests: unit tests for indicators, sizing, every risk rule, strategy
  signals on fixture data, order idempotency, reconciliation, mode gating, and
  live-endpoint refusal; integration tests against the simulated broker;
  an opt-in broker-sandbox test suite that never runs by default. Provide a
  CI workflow.
- Deployment: Dockerfile, compose file, a systemd unit or equivalent, backup
  and restore for the database, and a runbook covering daily start/stop,
  incident handling, and how to safely change parameters.

## SAFETY AND COMPLIANCE NOTES TO INCLUDE

- Educational/research disclaimer and a plain statement that paper results do
  not predict live results.
- No behavior that could be construed as market manipulation (no spoofing,
  layering, or order stuffing; rate-limit order submissions).
- Note PDT rules, futures margin and leverage risk, and that short selling has
  borrow and locate constraints.

## DELIVERABLE FORMAT (one-shot)

Produce, in this order:

1. **Assumptions and Deviations** — every assumption you made and anything
   you could not do, with the alternative you built.
2. **Architecture overview** — components, data flow, state machine for a
   trade, mode-gating diagram (text or Mermaid).
3. **Strategy portfolio summary** — one paragraph per strategy: edge
   hypothesis, regime(s) it is allowed in, entry/stop/exit rules, key params.
4. **Complete repository** — full file tree, then every file's full contents.
   No placeholders like "implement here"; if something must be stubbed, make it
   a working stub that fails closed and is documented as such.
5. **Tests** and how to run them, with expected output.
6. **README, runbook, acceptance-gate checklist, and live-readiness guide.**
7. **How to run** end-to-end: install, configure paper credentials, run
   backtests, run shadow, promote to paper-confirm, promote to paper-auto.
8. **Known limitations and next steps**, ranked by importance.

If the output would exceed your response limits, split it into clearly
numbered parts, keep file boundaries intact, and continue without being asked.
Do not summarize files you have already written; do not omit files.

## QUALITY BAR

Before finishing, re-read your own code adversarially and answer in writing:
- Can any code path place an order without passing the risk engine?
- Can any config or env combination reach a live endpoint unintentionally?
- What happens on restart mid-trade, on a stale feed, on a rejected order, on
  a partial fill at the daily loss limit?
- Which numbers in the system are hypotheses that the operator must validate?

Fix anything you find before delivering.
