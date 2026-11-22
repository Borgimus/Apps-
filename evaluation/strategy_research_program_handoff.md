# Strategy research program: handoff to the implementing agent

**Revised:** 2026-11-22 · **Base:** `agent/operational-recovery-2026-10-24` at `e1e6b7b`
**Owner request:** build and integrate new strategies so each can be tested on
this system. Start with a narrow first round, then work through the wider
Strategy Hypothesis Catalog (https://claude.ai/artifact/Lre4GBgqWY6S1xZbwnRN9p).

This revision replaces the first version of this handoff. The 30-idea build
now comes **after** a focused first round:

- three new stock signals,
- one slow ETF-rotation track,
- one implied-versus-realized volatility test.

Each is screened on years of history first and on the system's own
arrival-timed archive second. Two earlier lead-lag studies are now recorded
as already tested.

Read all of this file before writing code. Then read, in order:

- `HANDOFF.md`
- `evaluation/free_equity_research_2026_10_24.md`
- `evaluation/equity_portfolio_review_2026_11_03.md`
- `evaluation/one_contract_cohort_freeze.md`
- the two prior studies in section 5

---

## 1. The plan in one page

| Phase | What | Data | Why this order |
| --- | --- | --- | --- |
| **1. Historical screen** | S1 sector-relative reversal, S2 failed breakdown, S3 sector-confirmed relative strength, each against its own controls. R1 sector ETF rotation. V1 implied vs realized volatility (free stage) | 5–10 years of Alpaca 5-minute bars, yfinance daily, ^VIX | Hundreds of independent days. The 20-day archive alone can't separate an edge from luck |
| **2. Arrival-time check, then forward** | Survivors of phase 1 replayed in the order the data actually reached the VPS, then evaluated on every new session | `session-artifacts` raw events, which carry `observed_at` for every bar | Tests what history can't: real data latency and IEX gaps |
| **3. Wider catalog** | The remaining catalog ideas | Varies | Only after phases 1–2 are reported to the owner, or if the owner asks |

The first build is **one read-only replay tool**. It takes signal definitions
as plug-ins and runs them against two data sources: history, and the arrival
archive. Phase 3 ideas plug into the same tool later.

---

## 2. What "built and integrated" means

For each candidate, done means:

1. **Registered.** An entry in `evaluation/hypotheses/registry.json`, plus a
   pre-registration committed **before** any result is computed. The
   pre-registration states the mechanism, the exact rule, the controls, the
   statistic, and the pass and kill thresholds.
2. **Implemented** under `app/hypotheses/`, with unit tests that need no
   network.
3. **Screened** with the replay tool. The report is committed under
   `evaluation/hypotheses/<id>/`, whatever the result.
4. **Visible** on a read-only `/hypotheses` dashboard page.
5. **Wired forward, if it passes phase 1.** A post-session job evaluates the
   signal on each new day's archived events. It is never an order.

Integration never means a strategy can place a broker order. Paper orders are
an owner decision (stage 4, section 7). A candidate that fails is still done:
record it as `killed`, with its report.

---

## 3. Rules you must not break

These come from incidents in this project. Each one has cost sessions or
produced false evidence.

1. **No real money.** `LIVE_TRADING_ENABLED=false`. Don't add live order paths,
   deposits, paid subscriptions or account changes. Anything that costs money
   goes to the owner (section 13).
2. **Broker orders stay disabled.** The VPS runs a read-only observer, with the
   kill switch present and no armed marker. Nothing you ship may change that,
   even behind a default.
3. **Don't edit frozen files.** The running observer refuses to start if any
   of these change, which would end the live collection:
   - From `app/research/baseline.py` `FROZEN_FILES`:
     `app/strategies/opening_range_breakout.py`, `app/strategies/vwap_strategy.py`,
     `app/strategies/strategy_base.py`, `config/ticker_universe.yaml`,
     `app/research/equity_observer.py`, `app/research/baseline.py`.
   - Fingerprinted for the suspended options path: `config.yaml`,
     `requirements.lock`, `app/config/settings.py`,
     `evaluation/phase3_tracking.json`, and everything in `app/brokers/`.
   - Treat the frozen review contract
     (`evaluation/equity_portfolio_policy_2026_11_03.json` and
     `app/research/portfolio_review.py`'s outputs) as read-only.

   Import from these freely; never modify them. Run
   `frozen_baseline(Path('.'))` at the end of every work package. It must
   still pass.
4. **No new dependencies.** Available libraries are `numpy` 2.4, `pandas` 3.0,
   `scipy` 1.17, `yfinance` 1.5, `httpx` 0.28 and `fastapi`. Write
   Newey–West, the bootstrap and regressions yourself. Cache data as gzipped
   CSV or JSON.
5. **Never merge into the operational branch remotely.** The VPS installs a
   pinned commit through `scripts/ops/recover.py`, after 13:00 ET, with no
   observer running. Open draft PRs against
   `agent/operational-recovery-2026-10-24`, and the owner installs them.
6. **Leave the running protocol alone.** The IEX observer
   (`iex_equity_signal_screen_v1_2026_10_24`) and its review are partway
   through their checkpoints. Don't change their thresholds, universe, window,
   outputs or numbers. New candidates get their own review.
7. **No look-ahead.** This project has shipped two look-ahead bugs: a score
   read the next bar, and the regime check read a still-forming bar. A
   decision may use only bars available before it (section 6), and the tool
   must enforce that.
8. **Never hide failures.** `tests/test_no_silent_handlers.py` caps
   error-swallowing exception handlers per file, and new files start at zero.
   Missing data gives a null outcome, never a zero.
9. **Use aware datetimes only.** America/New_York for session logic, UTC for
   storage.
10. **Keep secrets out of the repo, logs and chat.** Tests use
    `httpx.MockTransport`.
11. **Report results as they are.** Give the sample size, the number of
    independent days, the t-statistic and returns in basis points. A screen
    result is a price-movement proxy, never "profitable".

---

## 4. Current state (2026-11-22)

- **VPS:** runs the zero-subscription IEX observer on 38 symbols, 09:30–12:30
  ET. Orders are disarmed, and there is no options data. ntfy notifications
  are live.
- **Code branch:** `agent/operational-recovery-2026-10-24` (PR #32, open). It
  contains #28, #29, #31 and #33. PR #30 is deliberately excluded.
- **Archive** (`session-artifacts`, through 2026-11-20): 20 trading days,
  made up of 18 full sessions, 1 partial and 1 invalid.
  - Full sessions: 858 eligible signals; 51 closed proxies in the frozen
    review. It has not reached its checkpoints yet.
  - Every bar records `observed_at`. On 2026-11-20, all 36 bars from
    09:30–12:30 arrived for 35 of the 37 symbols with bars that day.
  - Arrival delay after bar close: **median 31 s, 95th percentile 59 s,
    maximum 60 s** (the observer polls once a minute).
- **Evidence so far:** none of the following shows an edge.
  - Options experiment: 106 broker fills, about -$1,258.
  - ORB: did not predict the underlying.
  - Free observer: first five full sessions, -$16.46 at 10 bps.

---

## 5. Already tested: seed the registry with these

They count toward the multiple-testing total. Don't re-run them under new
names without a new mechanism.

| ID | Hypothesis | Result | Source |
| --- | --- | --- | --- |
| P1 | ORB (options and underlying) | Killed: 106 fills about -$1,258; direction-adjusted underlying returns negative at 5/15/30 min | `evaluation/one_contract_cohort_freeze.md`, `HANDOFF.md` |
| P2 | VWAP reclaim/rejection (options) | Killed with P1's cohorts | same |
| P3 | RSI trend | Never validated; diagnostic-only | `HANDOFF.md` |
| P4 | ES futures lead SPY | Killed. ES leads, but SPY absorbs the move within ±100 ms; every walk-forward configuration lost after costs, profit factor 0.58–0.63 | `leadlag/FINDINGS.md` on branch `claude/spy-dow-divergence-strategy-fs4sef` |
| P5 | DIA reverses before SPY (U-reversal) | Killed. On consolidated SIP data, SPY leads DIA, not the reverse | `ureversal/FINDINGS.md`, same branch |
| O1 | Frozen IEX observer (ORB/VWAP underlying proxy) | Running; forward. Not yours to change | `evaluation/free_equity_research_2026_10_24.md` |

The `leadlag/` and `ureversal/` code is reusable for data handling and
statistics. Read it before writing `stats.py`.

---

## 6. Architecture

Put everything in new packages, so nothing frozen moves.

```
app/research/replay/
  sources.py       BarSource protocol; HistoricalSource; ArchiveSource
  engine.py        chronological replay: availability-gated decisions, entries, exits
  controls.py      matched-control generators (same stock/time, sector ETF, inverse)
  portfolio.py     standalone and shared-slot replays using the frozen review's policy values
  stats.py         Newey–West, day-block bootstrap, halves/holdout/per-year checks
  costs.py         5/10/20 bps round trip per leg
  report.py        deterministic JSON + Markdown
app/research/history/
  bars.py          Alpaca historical 5-min bars (feed configurable), cache outside git
  daily.py         yfinance daily (SPY, sector ETFs, ^VIX, ^VIX9D)
  symbols.py       listing dates and ticker changes (e.g. XYZ was SQ until 2025)
app/hypotheses/
  registry.py      status machine and registry.json I/O
  base.py          HypothesisSignal + Hypothesis protocol
  s1_sector_reversal.py  s2_failed_breakdown.py  s3_relative_strength.py
  r1_sector_rotation.py  v1_implied_vs_realized.py
config/research/sector_map.yaml   symbol -> sector ETF (new file, not frozen)
app/api/hypotheses.py             read-only /hypotheses page and JSON
scripts/hypothesis.py             CLI: register | screen | archive-replay | forward | report | list
evaluation/hypotheses/registry.json and <id>/{preregistration.md,report.json,report.md}
```

**Availability is the central idea.** Every bar carries `available_at`:

- **ArchiveSource:** `available_at` is the archived `observed_at`, exactly.
  Replays process events in that order.
- **HistoricalSource:** `available_at = bar_end + 60 s`. That is the archive's
  measured maximum delay, so history is never more optimistic than the live
  system.

A decision made at time *d* sees only bars with `available_at ≤ d`. Entry is
the open of the first bar that **starts** at or after *d*. That matches the
frozen observer's rule. In practice it puts a 10:00 decision into the 10:05
bar.

The engine owns the clock. A hypothesis receives a view that raises an error
if it asks for anything later. A test must prove that a hypothesis which peeks
one bar ahead fails.

```python
@dataclass(frozen=True)
class HypothesisSignal:
    hypothesis_id: str
    symbol: str
    decided_at: datetime       # aware
    direction: int             # +1 long, -1 short (diagnostic only; replays are long-only)
    exits: tuple[str, ...]     # e.g. ("30m", "60m", "12:30")
    features: dict             # inputs actually used, for audit
    control_of: str | None = None   # set on control signals
```

**Portfolio replays.** Run every candidate three ways:

- **Standalone:** its own signals, no competition.
- **Against its controls:** the same rules applied to each control.
- **Shared slot:** together with frozen ORB/VWAP signals, under the frozen
  review's policy values (one slot, $1,000 notional, whole shares, three
  entries a day, 30-minute exit, $30 realized-loss gate).

Shared-slot results depend on which signal reached the slot first, so the
standalone result is the primary one. Read the policy values from
`evaluation/equity_portfolio_policy_2026_11_03.json` and don't copy them. Write
results to the candidate's own report, never into the frozen review.

**Registry status machine:**

```
registered -> screening -> killed
                        -> passed_phase1 -> archive_replay -> forward -> killed | passed_forward
                        -> blocked (reason required)
```

Every transition records a timestamp, a commit and a report path. Nothing is
ever deleted.

---

## 7. Stage gates

| Stage | What | Advances when | Who decides |
| --- | --- | --- | --- |
| 0 Register | Pre-registration committed before the data is examined | The commit exists; its order before the result commit is the proof | Agent |
| 1 Historical screen | Phase 1 test | Every criterion below holds | Agent |
| 2 Arrival-time replay | Phase 2 on the archive | Same sign and no worse than half the historical effect; no unresolved data | Agent |
| 3 Forward | Post-session evaluation on new days, to the pre-registered count | Pre-registered criteria hold on data collected after registration | Agent reports; owner reviews |
| 4 Paper orders | A disabled capability turned on for paper | **Owner only** | Owner |

**Stage 1 criteria.** Pre-register these unchanged unless the hypothesis says
why:

- The candidate's mean return **minus its primary control's**, net of costs on
  both, has a t-statistic of at least 3.0. Use Newey–West with lag equal to the
  horizon in bars, clustered by day, or a day-block bootstrap. The bar is high
  because this program now counts eight or more tests.
- The candidate's own mean return is above 0 at 10 bps and at least 0 at 20
  bps.
- The sign holds in both halves of the sample and in the last 30% held out.
- At least 100 independent signal days. Overlapping signals on one day count
  once for independence.
- No single year contributes more than 40% of the total.

**One primary specification per candidate.** If you want sensitivity checks,
pre-declare a grid of at most three values per parameter and report it. The
grid never decides pass or fail. A changed rule after seeing results is a new
ID with its own registration.

---

## 8. Phase 1 work packages

Each package is a draft PR stacked on the previous one, with the full suite
passing (`python -m pytest tests/ -q -p no:warnings`, exact counts reported).

**WP0: Orientation (no PR).**
- Run the suite on the base and record the counts.
- Run `frozen_baseline`.
- Read P4 and P5's code.
- With one small request each, check whether the owner's keys can read
  historical `feed=sip` bars (end more than 15 minutes in the past) as well as
  `feed=iex`. Use SIP for history if allowed; otherwise use IEX and note its
  thinner volume in every report.

**WP1: Data.**
- `bars.py`: bounded pagination with the observer's validation rules, a
  regular-session filter, gaps kept as gaps, and a cache under
  `TRADER_DATA_CACHE` (default `~/.cache/trader-history`).
- `daily.py`.
- `symbols.py`: listing dates, so ARM (2023) and RIVN, HOOD, COIN and AFRM
  (2021) have no rows before they listed, and the SQ → XYZ rename is joined.
- `sector_map.yaml`: committed and pre-registered **before** any S1/S3
  result. Proposed mapping:
  - SMH: NVDA, AMD, AVGO, ARM
  - XLK: AAPL, MSFT, SNOW, NET, CRWD, DDOG, PLTR, SHOP
  - XLY: AMZN, TSLA, RIVN, ROKU, DKNG, UBER, LYFT
  - XLF: COIN, HOOD, SOFI, AFRM, XYZ
  - QQQ, flagged as a weak fit: META, GOOGL, MSTR, MARA, RIOT

  Sector ETFs and index ETFs are never mapped to themselves.

**WP2: Replay tool.** Sources, engine, controls, portfolio, stats, costs and
reports, as in section 6. **Calibration tests are required before any
candidate runs:**
1. A planted 5 bps edge in synthetic bars is detected with t > 3.
2. Random entries on real cached SPY bars are rejected 95% or more of the
   time over 200 seeds.
3. A hypothesis that reads one bar ahead raises an error.
4. An `ArchiveSource` replay of 2026-11-20 reproduces the frozen observer's
   own ORB/VWAP signal IDs and timestamps for that day exactly.

Test 4 proves the tool sees what the observer saw.

**WP3: Registry, CLI and dashboard.**
- Seed P1–P5 and O1.
- Register S1, S2, S3, R1 and V1 before any screen runs.
- Add `/hypotheses`: read-only, following `app/api/equity_research.py`'s
  pattern and authentication.

**WP4: Phase 1 screens.**
- One commit per candidate, in this order: S2, S1, S3, R1, V1.
- Commit each report.
- Report S1 and S3 together (section 9).

**WP5: Phase 2 (only for survivors).**
- `archive-replay`: run over every archived session using `ArchiveSource`.
  Unresolved data blocks the result; it never counts as zero.
- `forward`: a post-session step that runs after publication succeeds. It
  reads that day's archived events and appends the candidate outcomes to its
  own artifact, under its own lock. Its failure must not affect the
  observer, the watchdog, publication or the frozen review, and tests must
  prove that.
- Add the step to `scripts/ops/research.sh`, and the job to `recover.py`'s
  equity job set, only in this PR.

---

## 9. Phase 1 specifications

Shared defaults:
- The window is 09:30–12:30 ET.
- Long-only in every replay; short mirrors are diagnostics only.
- First qualifying signal per symbol per day.
- No decisions after 11:30, so 60-minute exits complete by 12:30.
- Exits at 30 minutes, 60 minutes and 12:30. **The 30-minute exit is
  primary**, matching the frozen review.
- Costs of 5/10/20 bps round trip, charged on every leg, including controls.

### S1: Sector-relative reversal (long)

- **Question.** If NVDA or AMD drops while SMH holds up, does the stock tend to
  recover once it stabilizes? Does it do so more than an ordinary dip does?
- **Mechanism.** A drop not shared by the sector is more likely to be
  temporary order pressure than news, so liquidity providers are paid to
  absorb it. Published residual-reversal studies support this, with weaker
  results in recent years.
- **Rule.** At each available bar close from 10:00 to 11:30:
  - **Residual:** the stock's return from the 09:30 open, minus β × the sector
    ETF's return over the same window.
  - **β:** estimated from the prior 20 sessions of 5-minute returns, using
    prior data only.
  - **Signal when all hold:**
    - The residual is ≤ −1.5 σ. σ is the residual's prior-20-session standard
      deviation, scaled by √(elapsed bars).
    - The sector's own return from the open is ≥ −0.25%.
    - The latest bar did not make a new session low, and closed at or above
      its open (stabilized).
- **Primary control, ordinary dip.** Same stock, same stabilization rule, and
  a raw stock drawdown from the open at least as large as S1's median
  trigger drawdown. The sector condition is reversed: sector return < −0.25%.
- **Secondary control.** Same stock, random decision times drawn from the same
  10:00–11:30 window on days without a signal.
- **Primary statistic.** S1 minus the ordinary-dip control, 30-minute exit,
  net at 10 bps.
- **Kill criteria.** Any stage-1 criterion fails, or S1 does not beat the
  ordinary dip.

### S2: Failed breakdown below the opening range (long)

- **Question.** If SPY or QQQ breaks below its opening range and closes back
  inside, does it tend to keep recovering? Compare it with our ORB and with an
  inverse ORB.
- **Mechanism.** Breakdown stops get run and then absorbed. Trapped shorts
  cover on the reclaim.
- **Rule.**
  - **Opening range:** the 09:30–09:45 high and low, matching the frozen
    ORB's 15-minute range.
  - **Breakdown:** a completed bar closes below the range low.
  - **Failure:** within the next three completed bars, one closes back above
    the range low. The decision is when that reclaim bar becomes available,
    at 11:30 or earlier.
- **Primary symbols:** SPY and QQQ. Secondary: IWM and DIA, reported
  separately.
- **Primary control, inverse ORB.** Go long whenever the frozen
  `OpeningRangeBreakoutStrategy` (imported, unchanged, frozen parameters)
  issues a short breakdown signal.
- **Secondary controls.**
  - The frozen ORB long signal itself.
  - An unconditional long at the same clock time on days without a signal.
- **Primary statistic.** S2 minus inverse ORB, 30-minute exit, net at 10 bps.
  Also report maximum adverse excursion; no stop is modeled.
- **Prior expectation.** The inverse ORB lost less than ORB but still lost
  (P1). S2 must beat it, not just ORB.

### S3: Sector-confirmed relative strength (long)

- **Question.** If a stock is outperforming its sector at 10:00 and the sector
  is rising, does buying the stock add value over buying the sector ETF?
- **Mechanism.** Early leadership inside a rising sector reflects
  stock-specific demand that persists through the morning.
- **Rule.**
  - At the decision once the 09:55–10:00 bar is available, compute the stock's
    09:30-open → 10:00 return minus the sector ETF's over the same window.
  - Signal when that difference is ≥ +0.5% **and** the sector's return over
    the window is > 0.
  - Entry follows section 6, which in practice is the 10:05 bar open.
- **Primary control.** Buy the sector ETF at the same entry instead. The
  primary statistic is stock minus ETF.
- **Secondary control.** The same relative-strength condition on days when the
  sector is falling. This tests whether the sector confirmation adds anything.
- **Secondary variant (separate ID, S3b).** Measure from the prior close,
  including the overnight gap.

**S1 and S3 are a pair.** Both bet on a stock's return relative to its
sector, in opposite directions: S1 that a laggard catches up, S3 that a
leader keeps leading. Pre-register them together and say what each outcome
would mean.

- If both pass, check whether they fire in different conditions (time,
  sector trend). If they don't, suspect an artifact.
- If they look like mirror images, report that.
- Count them as two tests.

### R1: Sector ETF rotation (monthly)

- **Data:** yfinance daily for the eleven SPDR sector ETFs. XLRE starts in
  2015 and XLC in 2018; include each only once it exists.
- **Rule.** At each month end, rank sectors by their 12-month return
  excluding the most recent month. Hold the top 3, equal weight, for the next
  month.
- **Benchmarks.** Primary: equal weight across all available sectors,
  rebalanced monthly. Secondary: SPY.
- **Statistic.** Monthly excess return over the equal-weight benchmark, with a
  Newey–West t-stat (lag 3), maximum drawdown and turnover. Charge 10 bps per
  traded leg.
- **Stage-1 adjustment.** Use at least 180 months instead of 100 days. Its
  daily data is free, so run it alongside S1–S3.
- **Forward.** Record each month's holdings in its own artifact; no orders.
  Trading it would need stock orders and multi-week holds (section 12).

### V1: Implied versus realized volatility

- **Stage 1 (free).** Do options price more movement than follows?
  - Daily: ^VIX at the close against SPY's realized volatility over the next
    21 trading days (close-to-close, annualized).
  - Short horizon: ^VIX9D against the next 5 days.
  - Report the mean spread, the share of days with implied above realized,
    the distribution, and the worst 1% of periods (where realized exceeded
    implied), by year.
- **Why it matters.** V1 is the concrete reason to buy OPRA data. Stage 2
  measures short-dated SPY implied volatility at 09:45 (0–7 days to expiry)
  against realized movement over each option's life, from forward-recorded
  quotes.
- **Stage 2 status.** `blocked: needs OPRA options data (owner decision)`.
  Build and mock-test the collector anyway.

---

## 10. Phase 2 details

- **The archive replay runs only for phase 1 survivors.** With about 20
  sessions it can't establish an edge. It checks two things:
  - that the signal survives actual arrival times and IEX gaps;
  - that its archive behavior is not grossly different from history (same
    sign, at least half the effect).
- **Forward evaluation** runs to a pre-registered count. Default: 60 new full
  sessions or 100 candidate signals, whichever comes later. It is judged on
  data collected **after** registration only.
- **After forward:** checking whether a survivor holds up against realistic
  execution needs timestamped bid/ask quotes. Write that as a proposal for the
  owner, with the quote source and its cost.

---

## 11. Phase 3: the wider catalog (deferred)

Start only after phases 1–2 are reported to the owner, or if the owner asks.
Each idea plugs into the same replay tool and registry, and each gets its own
registration. Summary of the catalog:

| Group | IDs (catalog) | First test | Blocker |
| --- | --- | --- | --- |
| Intraday price | A1 intraday momentum (last half hour), A2 ORB on high-relative-volume stocks, A3 gaps, A4 intraday seasonality, A5 ETF pairs, A6 regime filters | History | A1/A2 need an exit after 12:30; A2 and A4 need a wider universe |
| Calendar and events | B1 overnight drift, B2 pre-FOMC, B3 turn of month, B4 expiration pinning, B5 earnings day, B6 macro mornings | Daily or 5-minute history; B4 forward open interest | Overnight and multi-day holds for B1/B3 |
| Options and volatility | C2 day vs night option returns, C3 weekend decay, C4 earnings volatility, C5 VIX roll, C6 selling 0DTE, C7 dealer gamma | Forward-recorded quotes | OPRA for most; multi-leg orders to trade C1/C3/C4/C6 |
| Information | D1 headlines, D2 insider clusters, D3 congressional trades, D4 search attention, D5 weather (a control that must fail) | History where available | Data access for D3/D4 |
| Other horizons | E1 multi-asset trend, E2 crypto | Daily history | Stock orders; 24/7 operation |
| Models | F1 ridge/logistic ranker (numpy only), F2 language-model trader (forward only) | History; forward | Overfitting controls |
| No code | C8 dispersion, C9 unusual options activity, G microstructure | Feasibility notes only | Data and access not available |

The catalog page linked at the top has the mechanism, evidence and kill
criteria for each.

---

## 12. Capabilities that need frozen files

Build these only as **separate draft PRs**, labelled `DO NOT INSTALL —
requires owner decision and a new cohort declaration`. Every behavior
defaults off and is tested off. Don't regenerate baseline hashes in them.

| Capability | Files | Unlocks |
| --- | --- | --- |
| A research window after 12:30 | A new separately fingerprinted window. Never edit the frozen observer's | A1, A2, B2 |
| Stock orders (paper only) | `app/brokers/*` | Trading R1, S1–S3, B1, E1 |
| Overnight and multi-day holds | `app/trading/exit_manager.py`, `app/config/settings.py` | R1, B1, B3 |
| Defined-risk multi-leg options | `app/brokers/*`, `app/risk/risk_manager.py`, shadow replay | Trading V1 / C1, C3, C4, C6 |

Phase 1 needs none of these.

---

## 13. Owner decisions

Stop at these and put them in the PR description:

1. **OPRA options data** (a paid Alpaca plan; confirm the price). Unblocks V1
   stage 2 and most of the options catalog.
2. **Timestamped bid/ask quotes** for execution checks after forward.
3. Installing any PR on the VPS, including the forward step.
4. **Moving to phase 3**, or adopting any capability in section 12.
5. Stage 4 paper orders for any candidate.
6. Anything involving live trading or a funded account. The answer is no
   unless the owner initiates it in writing.

---

## 14. Pitfalls

| Pitfall | Guard |
| --- | --- |
| Look-ahead (twice in this project) | Engine-owned clock; peeking test; history uses the 60 s availability delay |
| A small archive read as evidence | Phase 1 runs on years of history; the archive only checks realism |
| Survivorship and ticker changes | `symbols.py` listing dates; SQ → XYZ join |
| Paired hypotheses read as independent wins | S1/S3 registered and reported together |
| Shared-slot competition decides results | Standalone replay is primary |
| Tuning after seeing results | One primary spec; any change is a new ID |
| Overlapping signals inflating the sample | Day-clustered statistics; independent-day counts |
| Silent data gaps | Null outcomes, unresolved blocks, the ratchet test |
| Disturbing the live observer | Frozen-baseline check every WP; post-session step isolated and tested |
| Remote merges breaking the VPS | Draft PRs only; the owner installs |

---

## 15. Reporting back

At the end of each work package, the PR description and your message to the
owner state:

- What was built, and exact test counts.
- The frozen-baseline result.
- The registry table: each ID with its status, primary statistic,
  independent days and t.
- What is blocked, and on which owner decision.
- What was not exercised. For example, real API calls, VPS installation or
  cron.

Lead with failures. The owner asked for a realistic view of whether anything
here can be profitable, and the honest default is that most of it won't be.
