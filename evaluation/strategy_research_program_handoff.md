# Strategy research program: handoff to the implementing agent

**Written:** 2026-11-22 · **Base:** `agent/operational-recovery-2026-10-24` at `e1e6b7b`
**Owner request:** build and integrate every strategy in the Strategy Hypothesis
Catalog (https://claude.ai/artifact/Lre4GBgqWY6S1xZbwnRN9p) so each one can be
tested on this system.

Read all of this file before writing code. Then read, in order: `HANDOFF.md`,
`evaluation/free_equity_research_2026_10_24.md`,
`evaluation/equity_portfolio_review_2026_11_03.md`, and
`evaluation/one_contract_cohort_freeze.md`.

---

## 1. What "built and integrated" means

For every hypothesis in section 7, done means **all** of the following:

1. **Registered.** It has an entry in `evaluation/hypotheses/registry.json`,
   plus a pre-registration file written before any result was looked at. The
   file states the mechanism, the exact rule, the data, the statistic, and the
   pass and kill thresholds.
2. **Implemented** as code under `app/hypotheses/`, with unit tests that need
   no network.
3. **Screened.** It has been run through the research harness, on history where
   history exists, and its report has been committed under
   `evaluation/hypotheses/<id>/`.
4. **Visible.** Its status and latest report appear on the read-only
   `/hypotheses` dashboard page.
5. **Wired forward, if it passes the screen** (or if it can only be tested
   going forward). That means a forward collector or shadow observer that
   records prospective outcomes. These are never orders.

"Integrated" never means the strategy can place a broker order. Paper orders
for any hypothesis are an owner decision (stage 4 in section 5). Building the
capability behind a disabled flag is in scope. Turning it on is not.

A hypothesis that fails its screen is still done. It is recorded as `killed`
with its report. Failing is the expected outcome for most of them, and the
registry exists to keep that count honest.

---

## 2. Rules you must not break

These come from incidents in this project's history. Each one has cost sessions
or produced false evidence.

1. **No real money, ever.** `LIVE_TRADING_ENABLED=false`. Never add a code path
   that places a live order. No deposits, no paid subscriptions, no account
   changes. Anything that costs money goes on the owner-decision list
   (section 9).
2. **Broker orders stay disabled.** The deployed system is a read-only research
   observer with the kill switch present and no armed marker. Nothing you ship
   may arm it, remove the kill switch, or enable orders, even behind a default.
3. **Do not touch frozen files.** The running observer refuses to start if any
   of these change, which would end the live collection:
   - From `app/research/baseline.py` `FROZEN_FILES`:
     `app/strategies/opening_range_breakout.py`, `app/strategies/vwap_strategy.py`,
     `app/strategies/strategy_base.py`, `config/ticker_universe.yaml`,
     `app/research/equity_observer.py`, `app/research/baseline.py`
   - Fingerprinted for the suspended options path: `config.yaml`,
     `requirements.lock`, `app/config/settings.py`,
     `evaluation/phase3_tracking.json`, and everything in `app/brokers/`.

   You may import from frozen modules. You may not edit them. New code goes in
   new files. Section 8 lists the capabilities that genuinely need a frozen
   file. Those go in separate, clearly labelled PRs that are never installed
   without the owner.
4. **No new dependencies.** `requirements.lock` is frozen. Available libraries
   are `numpy` 2.4, `pandas` 3.0, `scipy` 1.17, `yfinance` 1.5, `httpx` 0.28 and
   `fastapi`. There is no statsmodels, scikit-learn or pyarrow. Write
   Newey–West, the bootstrap and ridge or logistic regression in numpy/scipy.
   Cache data as gzipped CSV or JSON, not parquet.
5. **Never merge into the operational branch remotely.** The VPS installs a
   pinned commit through `scripts/ops/recover.py`, after 13:00 Eastern, with no
   observer running. A remote merge or force-push makes the host checkout
   disagree with its branch. In September that refused two sessions. Open PRs
   as drafts against `agent/operational-recovery-2026-10-24` and let the owner
   install.
6. **Don't change the running protocol.** The free IEX observer
   (`iex_equity_signal_screen_v1_2026_10_24`) and its portfolio review are
   pre-registered and partway through their checkpoints (10 and 20 full
   sessions, then a 10-session holdout). Don't edit their thresholds, universe,
   windows or outputs. New collectors run beside the observer, never inside it.
7. **No look-ahead.** This project has shipped look-ahead bugs before:
   - A quality score read the bar after the signal.
   - The regime check read a still-forming bar.

   Every signal must be computable from bars that had **closed** before its
   timestamp. Entry is the next bar's open at the earliest. Every hypothesis
   needs a test that proves this (see section 6, WP2).
8. **Never hide a failure.** `tests/test_no_silent_handlers.py` caps the number
   of broad exception handlers that swallow errors, per file, and new files
   start at zero. A data gap produces a null outcome, never a zero. A
   degraded run is labelled degraded.
9. **Use aware datetimes.** Everything is timezone-aware, America/New_York for
   session logic and UTC for storage. A naive `utcnow()` once disabled a
   staleness check for months.
10. **Never put secrets in the repo, logs or chat.** Credentials live in the
    host `.env`, and the owner enters them there. In tests, use
    `httpx.MockTransport` and never real keys.
11. **Report results as they are.** Give exact test counts. Say what was not
    exercised. State a hypothesis's result in basis points with its sample
    size, its independent-day count and its t-statistic. Never write
    "profitable" for a screen result: it is a price-movement proxy, not a fill.

---

## 3. Current state (2026-11-22)

- **VPS:** runs the zero-subscription IEX equity observer on 38 symbols,
  09:30–12:30 ET. Orders are disarmed, and no options data is available.
  Tradier deactivated the account on 2026-09-22, and no OPRA subscription has
  been bought. ntfy notifications are live (PR #33).
- **Branches:**
  - `agent/operational-recovery-2026-10-24` is the maintained code branch and
    PR #32 (open). It already contains #28, #29, #31 and #33.
  - PR #30 (signal-quality and loss-capacity semantics) is deliberately
    excluded. Don't build on it unless the owner says so.
- **Evidence so far:**
  - The options experiment lost: 106 broker fills, about -$1,258.
  - ORB signals did not predict the underlying.
  - The free observer's first five full sessions: 260 eligible signals and 15
    replay proxies, giving -$16.46 at 10 bps.
  - No edge has been demonstrated anywhere.
- **Key modules:**
  - `app/research/equity_observer.py` (IEX bar client, causal signal
    discovery)
  - `app/research/portfolio_review.py` (chronological one-slot replay)
  - `app/evaluation/shadow_book.py` and `shadow_replay.py` (options
    counterfactuals)
  - `app/trading/exit_manager.py`
  - `app/operations/` (health, monitor, artifacts)
  - `scripts/ops/recover.py` (installer)
  - `app/api/equity_research.py` (dashboard pattern to copy)

---

## 4. Architecture to build

Use new packages so nothing frozen moves:

```
app/hypotheses/
  registry.py        Hypothesis records, status machine, registry.json I/O
  base.py            HypothesisSignal + Hypothesis interface (below)
  a_intraday/        one module per hypothesis, e.g. a1_intraday_momentum.py
  b_calendar/
  c_options/
  d_information/
  e_other/
  f_models/
app/research/history/
  bars.py            Alpaca historical stock bars (feed configurable), on-disk cache
  daily.py           yfinance daily history (SPY, ETFs, ^VIX, ^VIX3M), cache
  events.py          FOMC/CPI/NFP/earnings/expiration calendars (static files + yfinance)
  costs.py           cost scenarios (5/10/20 bps round trip, plus option-spread model)
  engine.py          event study: signal -> next-bar entry -> horizon returns
  stats.py           Newey–West t, day-block bootstrap, walk-forward splits
  report.py          JSON + Markdown report, deterministic
app/research/forward/
  collector.py       shared prospective collector (lock, heartbeat, artifacts)
  oi_snapshot.py     B4/C7 open interest + gamma by strike
  straddle_shadow.py C2/C3/C6 quote recorder (needs OPRA; mock-tested until then)
  news.py            D1 headline capture
app/api/hypotheses.py      read-only /hypotheses page + /api/hypotheses JSON
scripts/hypothesis.py      CLI: register | screen <id> | report <id> | list
evaluation/hypotheses/
  registry.json
  <id>/preregistration.md, <id>/report.json, <id>/report.md
```

**Interface.** Keep it independent of the frozen `StrategyBase`, which you may
subclass but not edit:

```python
@dataclass(frozen=True)
class HypothesisSignal:
    hypothesis_id: str
    symbol: str
    decided_at: datetime      # aware; last *closed* bar end used for the decision
    direction: int            # +1 long, -1 short, 0 = volatility/neutral
    horizon: timedelta | str  # e.g. 30 min, "close", "next_open", "20d"
    expression: str           # "stock" | "option_long" | "straddle" | "credit_spread" | ...
    features: dict            # inputs actually used, for audit

class Hypothesis(Protocol):
    id: str; family: str; data_needs: tuple[str, ...]
    def signals(self, data: "HistoryView") -> Iterable[HypothesisSignal]: ...
```

`HistoryView` exposes only data at or before the `decided_at` being evaluated.
The engine enforces this, so a hypothesis cannot read the future even by
mistake.

**Registry status machine:**

```
draft -> registered -> screening -> killed
                                 -> passed_screen -> forward -> killed | passed_forward
                                 -> blocked (data or owner decision; reason required)
```

Every transition records a timestamp, a commit and a report path. Nothing is
ever deleted from the registry.

**Forward collectors** run as their own cron job, `scripts/ops/research.sh
hypotheses`, with their own lock. They start after the observer is healthy,
and each writes its own artifacts. A collector failure must not affect the
observer, its watchdog or its reports. Add the cron entry in `recover.py`'s
equity job set only in a PR whose tests prove that isolation.

---

## 5. Stage gates (the same for every hypothesis)

| Stage | What happens | Pass to the next stage when | Who decides |
| --- | --- | --- | --- |
| 0 Register | Pre-registration committed before any data is examined | File exists and is committed | Agent |
| 1 Screen | Historical test on the underlying (or the free proxy) | The criteria below all hold | Agent |
| 2 Expression | Options or stock expression tested on recorded quotes, including spreads | Positive after realistic spread and cost | Agent; needs data |
| 3 Forward shadow | Prospective collection under a written protocol, 100–200 outcomes | Pre-registered criteria hold on data collected after registration | Agent reports; owner reviews |
| 4 Paper orders | Disabled capability switched on for paper only | **Owner decision only** | Owner |

**Stage 1 pass criteria.** Pre-register them unchanged unless the hypothesis
has a stated reason to differ:

- t-statistic ≥ 3.0 (Newey–West with lag equal to the horizon in bars, or a
  day-block bootstrap). This is the multiple-testing bar from Harvey, Liu & Zhu
  (2016). It applies because this program tests dozens of ideas.
- Mean direction-adjusted return > 0 after the **10 bps** round-trip cost, and
  ≥ 0 after 20 bps.
- The sign holds in **both** halves of the sample, and in the held-out final
  30% (or the post-publication period, for published effects).
- At least 100 independent days with a signal. Overlapping signals on the same
  day count once for independence.
- No single year contributes more than 40% of the total.

Report every screen, passed or failed. Never re-run a killed hypothesis with
tweaked parameters under the same ID. A variant gets a new ID and its own
registration, and the registry counts it as another test.

---

## 6. Work packages, in order

Each package is a separate draft PR stacked on the previous one, with the full
suite passing. Run `python -m pytest tests/ -q -p no:warnings` and report the
exact counts.

### WP0: Orientation (no PR)
- Run the full suite on the base commit and record the counts.
- Run `frozen_baseline(Path('.'))` from `app/research/baseline.py` and confirm
  it passes. Re-run it at the end of every WP; it must still pass.
- Confirm which Alpaca historical feeds the owner's keys can read. Use one
  small request each for `feed=iex` and `feed=sip`, with an end time older
  than 15 minutes. Record the result in WP1's PR. If `sip` is refused, use IEX
  and record IEX's coverage bias in every report.

### WP1: History data layer
- `bars.py`: bounded pagination (copy the observer's validation rules), aware
  timestamps, regular-session filter, and a cache under `TRADER_DATA_CACHE`
  (default `~/.cache/trader-history`, outside git). Gaps stay gaps.
- `daily.py`: yfinance daily history for SPY, QQQ, IWM, DIA, sector ETFs, TLT,
  GLD, DBC, UUP, ^VIX and ^VIX3M, with a dividend/split adjustment flag.
- `events.py`: committed static CSVs of FOMC decision dates and times, CPI and
  NFP release dates, and monthly/quarterly expirations, each with its source
  URL in a header comment. Earnings dates come from yfinance.
- Tests: mocked pagination, gap handling, cache round-trip, timezone
  correctness.

### WP2: Engine, statistics, costs and calibration
- `engine.py`: for each signal, entry is the first bar opening at or after
  `decided_at`. Exit is at the horizon close (or the next open or close for
  daily horizons). Returns are direction-adjusted, in bps. A missing
  constituent bar gives a null outcome.
- `stats.py`: Newey–West mean t-stat, day-block bootstrap CI, walk-forward and
  holdout splits, and the per-year contribution check.
- `costs.py`: the 5/10/20 bps scenarios, plus an option-spread model that takes
  recorded bid/ask when available.
- **Calibration tests (required before any hypothesis runs):**
  1. A synthetic series with a planted 5 bps edge is detected (t > 3).
  2. A random signal on real cached SPY bars is rejected, 95%+ of the time
     over 200 seeds.
  3. **A look-ahead canary:** a hypothesis that peeks one bar ahead must raise
     an error from `HistoryView`, not return a result.
  4. D5 (weather) is rejected. See section 7.

### WP3: Registry, CLI, reports and dashboard
- `registry.py` and the status machine, `scripts/hypothesis.py`, and
  deterministic reports (identical inputs give identical bytes).
- `/hypotheses`: a read-only table of id, family, status, last screen
  statistic, days and t; a detail page per hypothesis. Copy the pattern and
  authentication of `app/api/equity_research.py`. No state-changing routes.
- Register all hypotheses from section 7 as `registered` in this WP, before
  any screen runs.

### WP4: Historical screens
Implement and screen, one commit per hypothesis: A1, A3, A4, A5, A6, B1, B2,
B3, B5, B6, C1 (stage 1 only), C5, D5, E1, E2, F1. Then A2, which needs the
wide-universe data in WP1 extended to roughly 1,000 symbols, so do it last in
this WP. Commit each report, whatever the result.

### WP5: Forward collectors
- `oi_snapshot.py` (B4, C7): runs once after 09:35 and again at 15:55 if the
  session window allows. It needs only the chain endpoint, which works without
  OPRA for open interest. Verify that, and mark the collector blocked if not.
- `straddle_shadow.py` (C2, C3, C6): records ATM straddle and condor quotes.
  Build it and test it against `httpx.MockTransport`. Its registry status is
  `blocked: needs OPRA options data (owner decision)` until the owner
  subscribes.
- `news.py` (D1): Alpaca news API. Verify availability on the owner's plan
  with one request. Score headlines with a deterministic, versioned scorer: a
  keyword or lexicon model first. A language-model scorer is a separate
  hypothesis ID, run offline, and its model version and prompt are recorded
  in the registration.
- All collectors honour section 4's isolation rules.

### WP6: Trading capabilities behind disabled flags
These need frozen files, so each is its **own draft PR**. Each is labelled `DO
NOT INSTALL — requires owner decision and a new cohort declaration`, with every
new behavior defaulted off and tested off:

1. **A later session exit** (to 15:55) as a new, separately fingerprinted
   research window. Do not edit the frozen observer's 12:30 window. Unlocks A1,
   A2, B2 and F1 forward tests.
2. **Stock orders** on the broker interface (paper only, refused when live).
   Unlocks the stock expressions of A1, B1, C2 hedging, C5, D2 and E1.
3. **Overnight and multi-day holds:** EOD liquidation exemptions,
   reconciliation across days, and daily-limit accounting.
4. **Defined-risk multi-leg options** (verticals, iron condors, straddles):
   order types, per-leg partial fills, a max-loss risk model for short legs,
   and replay support. This is the largest item. It unlocks C1, C3, C4 and C6.

### WP7: Feasibility notes instead of code
Write `evaluation/hypotheses/<id>/feasibility.md` for C8 (dispersion), G
(microstructure), C9 (unusual activity: needs trade-level OPRA with
opening/closing flags, which isn't public) and D3/D4 if their data turns out to
be inaccessible. Each note says what data or access would make the idea
testable and what it would cost. Register them as `blocked` with the reason.

---

## 7. Hypothesis specifications

Write these into the pre-registrations. Default horizons and rules are given.
If you change one before registering, say why in the file. Never change one
after registering. Sources are named so you can look them up; verify any
figure you quote from them.

| ID | Rule to implement | Data | Stage-1 statistic | Infra for forward/trade |
| --- | --- | --- | --- | --- |
| **A1** Intraday momentum (Gao, Han, Li & Zhou 2018; Baltussen et al. 2021) | r1 = prior close → 10:00 return on SPY. Signal sign(r1) at 15:30, exit 16:00. Also the variant with r1 and the 15:00–15:30 return | SPY 30-min bars, 10+ yrs | Mean last-half-hour return × sign(r1); slope of a regression; pre/post 2018 | Later window (WP6.1); stock orders |
| **A2** ORB on stocks in play (Zarattini, Barbon & Aziz 2024, working paper) | Daily top 20 by opening-15-min relative volume among ~1,000 liquid stocks. 5-min ORB, stop 10% of 14-day ATR, exit at close. Compare against 20 random stocks from the same pool | 5-min bars, ~1,000 symbols, 2–3 yrs | Top-RVOL minus random-slice return | Wider universe; later window |
| **A3** Gap fade / gap-and-go | Buckets by gap size (±0.25/0.5/1/2%) and pre-market volume rank. Fade below the threshold, follow above. Entry 9:35, exit 12:30 | 5-min bars + daily | Per-bucket mean; thresholds fixed on the first 70% | Ready |
| **A4** Intraday seasonality (Heston, Korajczyk & Sadka 2010) | For each half-hour interval, rank symbols by the mean same-interval return over the prior 20 days. Long the top quintile, short the bottom, for that interval next day | 30-min bars, 38 then 500 symbols | Long-short spread per interval | Stock orders, short |
| **A5** ETF pairs (Gatev, Goetzmann & Rouwenhorst 2006) | Pairs SPY/IWM, XLK/QQQ, SMH/XLK, DIA/SPY. 60-bar rolling z of the log spread. Enter at abs(z) > 2, exit at z = 0 or 12:30 | 5-min bars | Spread return per trade | Two-leg stock positions |
| **A6** Regime filter on ORB/VWAP | Recompute frozen ORB/VWAP signals on history (import, don't edit). Pick one filter (ATR percentile, trend or VIX level) on 2016–2021, judge it once on 2022–26 | 5-min bars, ^VIX | Filtered minus unfiltered return in the holdout | Ready |
| **B1** Overnight drift (Cliff, Cooper & Gulen 2008; Lou, Polk & Skouras 2019) | Close → next open versus open → close on SPY | Daily since 1993 | Mean overnight minus intraday; last-10-year subsample | Overnight holds; stock orders |
| **B2** Pre-FOMC drift (Lucca & Moench 2015) | SPY open → 13:55 on FOMC decision days versus other days | 30-min bars + FOMC calendar | Difference in means; post-2015 subsample | Later window |
| **B3** Turn of month / pre-holiday (Ariel 1987; Lakonishok & Smidt 1988) | Days −1 to +3 around the month turn; the day before holidays | Daily since 1993 | Mean on effect days minus others; pre/post 2010 | Multi-day holds |
| **B4** Expiration pinning (Ni, Pearson & Poteshman 2005) | Distance of the 15:55 SPY price to the max-OI strike versus a random strike from the same chain | Forward OI snapshots (WP5) | Mean distance difference, ≥ 60 days | Forward collector |
| **B5** Earnings-day reaction (Bernard & Thomas 1989; Martineau 2022) | Day-1 return 9:45 → 12:30, in the direction of the EPS surprise sign | yfinance EPS actual/estimate + 5-min bars | Mean signed return | Observe-only on earnings days |
| **B6** Macro release mornings | 9:30–10:00 versus 10:00–12:30 return on CPI and NFP days; test reversal | 5-min bars + release calendar | Correlation of the two windows | Ready |
| **C1** Volatility risk premium (Carr & Wu 2009; Bondarenko 2014) | Stage 1: ^VIX at t versus SPY realized vol over the next 21 trading days (annualised); frequency and size of VIX > RV. Stage 2+: SPY put credit spreads | Daily since 1990; then options quotes | Mean (VIX − RV), the share of positive days, and the worst 1% months | Multi-leg (WP6.4); options data |
| **C2** Day vs night option returns (Muravyev & Ni 2020) | ATM SPY straddle bought at 9:45 and sold at 15:45, delta-hedged hypothetically with stock every 30 min; compare overnight holding | Forward OPRA quotes | Mean hedged P&L per day after the recorded spread | Collector (blocked on OPRA) |
| **C3** Weekend decay (Jones & Shemesh 2018) | ATM SPY straddle value Friday 15:55 → Monday 9:35 versus a weekday overnight | Forward OPRA quotes | Mean change versus weekday | Collector (blocked on OPRA) |
| **C4** Earnings volatility run-up / crush (Gao, Xing & Zhang 2018) | Two IDs: C4a long straddle T−5 → T−1 close; C4b short straddle T−1 close → T+1 open | Historical options data (owner purchase) or forward IV records | Mean return per event | Multi-day + multi-leg |
| **C5** VIX futures roll | Hold short-vol exposure when ^VIX/^VIX3M < 1, flat otherwise. Proxy the return with daily inverse-VIX ETP data where available; report the Feb-2018 drawdown explicitly | Daily | Return, max drawdown, worst day | Stock orders; multi-day |
| **C6** Selling 0DTE | Defined-risk SPY 0DTE iron condor opened at 10:00 (short strikes ~10 delta, wings $5), held to 15:55 | Forward OPRA quotes | Mean P&L after the spread; worst day | Collector (blocked on OPRA) |
| **C7** Dealer gamma regime (Ni, Pearson, Poteshman & White 2021) | Net gamma × OI × 100 × S² across the SPY chain at 9:35, sign-assumed (dealers short puts, long calls). Predict that day's range and A1's success | Forward OI + Greeks | Correlation with realized range | Collector (Greeks need OPRA; OI may not) |
| **D1** Headline sentiment (Lopez-Lira & Tang 2023) | Score each headline on the 38 symbols. Trade from the next bar open, 30 and 60 min, in the sign of the score | Alpaca news history + 5-min bars | Signed mean return | News collector |
| **D2** Insider cluster buys (Cohen, Malloy & Pomorski 2012) | ≥ 3 distinct insiders buying open-market within 10 days, in liquid names; returns at 21 and 63 trading days | SEC EDGAR Form 4 (free; set a descriptive User-Agent with no personal data) + daily | Mean abnormal return versus SPY | Multi-day; stock orders |
| **D3** Congressional trades (Belmont et al. 2022) | Buy on the disclosure date, hold 21/63 days | Public disclosure datasets, if accessible without payment | Abnormal return | Feasibility note if no free data |
| **D4** Search attention (Da, Engelberg & Gao 2011) | Weekly search spike → next-week return | Google Trends, if programmatically accessible | Mean return | Feasibility note if blocked |
| **D5** Weather control (Hirshleifer & Shumway 2003) | NYC morning cloud cover → SPY open-to-close | Free historical weather API (record the source) | Must **fail**. If it passes, stop and audit the engine | Control only |
| **E1** Trend following (Moskowitz, Ooi & Pedersen 2012) | Monthly: hold each of SPY/TLT/GLD/DBC/UUP if its 12-month return > 0, equal risk weight | Daily ETFs since 2005 | Sharpe, drawdown versus 60/40 | Stock orders; multi-week |
| **E2** Crypto momentum (Liu & Tsyvinski 2021) | BTC/ETH: long if the 1-week return > 0, weekly rebalance; also time-of-day | Alpaca crypto bars | Mean weekly return | 24/7 operation |
| **F1** ML ranker (Gu, Kelly & Xiu 2020 for method) | Ridge/logistic regression in numpy on scanner-style features (RVOL, ATR%, gap, trend, VWAP distance, time of day) → 30-min return sign. Train 2016–22, freeze, test 2023–26 **once** | 5-min bars | Holdout hit rate and mean signed return | Later window |
| **F2** Language-model trader | Forward only. Every decision is logged with its model, prompt version and inputs; scored by stage-3 rules | Forward | Pre-registered | Register; build last |
| **C8, C9, G** | No code: feasibility notes (WP7) | n/a | n/a | n/a |

---

## 8. Frozen-file map for WP6

| Capability | Frozen files it must change | Why it can't go elsewhere |
| --- | --- | --- |
| Later session window | `config.yaml` (`position.eod_exit_time`), or a new research window outside the frozen observer | The frozen observer's 09:30–12:30 window is its protocol |
| Stock orders | `app/brokers/broker_interface.py`, `alpaca_broker.py`, `paper_broker.py` | The broker interface only has `place_option_order` |
| Multi-day holds | Exit manager plus `app/config/settings.py` for the flags | EOD liquidation and daily limits assume flat at the close |
| Multi-leg options | Broker adapters, `app/risk/risk_manager.py` (assumes premium = max loss), shadow replay | Short legs need a different risk model |

Each WP6 PR must keep the frozen baseline check failing loudly if installed by
mistake. Do not regenerate baseline hashes in these PRs. The owner declares a
new cohort when they choose to adopt one.

---

## 9. Decisions only the owner can make

Put any of these that come up in your PR descriptions, and stop at them rather
than working around them.

1. **Options data (OPRA).** Unblocks C2, C3, C6, C7's Greeks and every stage-2
   options test. It's a paid Alpaca market-data plan; confirm the current
   price with Alpaca. `evaluation/alpaca_options_provider_rollout.md` has the
   procedure.
2. **Historical options data** from a vendor. Unblocks full tests of C1–C4
   without months of forward collection.
3. Installing any PR on the VPS, including new cron jobs.
4. Adopting a WP6 capability or declaring a new cohort.
5. Stage 4 (paper orders) for any hypothesis.
6. Anything involving live trading or a funded account. The answer is no
   unless the owner initiates it in writing.

---

## 10. Pitfalls this project has already hit

| Pitfall | What happened | Guard |
| --- | --- | --- |
| Look-ahead | VWAP quality read the next bar; the regime read a forming bar | `HistoryView` enforcement + canary test |
| Silent failure | Tradier 401s ran for 7 sessions reported as clean | Null outcomes, degraded labels, ratchet test |
| Naive time | `utcnow()` read as local time disabled staleness for months | Aware datetimes only; tests in two timezones |
| Overlap counted as independent | Overlapping horizons inflated sample size | Report independent days; block statistics |
| Retrospective "pre-registration" | A replay declared after the data it judged | Commit the registration before running the screen; the commit order is the proof |
| Pooling cohorts | Mixed providers and policies into one total | One cohort per protocol; never sum across |
| Remote merges | The VPS refused sessions after a branch moved | Draft PRs only; owner installs |
| Threshold changes mid-run | Amendments appended to tracking files | New ID, new registration |
| Small samples read as edge | 27 trades at +$113 treated as promising | ≥ 100 independent days at stage 1 |

---

## 11. How to report back

At the end of each WP, the PR description (and your message to the owner)
states:

- What was built.
- Exact test counts.
- The frozen-baseline check result.
- The registry table: each ID with its status and key statistic.
- What is blocked and on which owner decision.
- What was not exercised. For example, real API calls, VPS installation or
  cron.

Lead with the results that killed hypotheses; don't bury them. The owner has
asked for a realistic view of whether anything here can be profitable, and
the honest default answer is that most of it won't be.
