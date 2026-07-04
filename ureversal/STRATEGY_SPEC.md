# SPY/DIA Divergence U-Reversal Strategy — Formal Specification

Version 1.0 · Status: **RESEARCH — live trading gated on validation results**

---

## 1. Overview

Long-only SPY mean-reversion strategy traded exclusively in the opening window
**09:33:00–09:53:00 ET**. The hypothesis: when SPY and DIA decline together off
the open, DIA sometimes reverses upward *before* SPY completes the bottom of a
short-term "U"; that DIA reversal is a leading signal for the right side of
SPY's U.

This document defines every signal mathematically. The same definitions are
implemented once, in `ureversal/signals.py`, and consumed unchanged by the
research study, the backtester, the replay system, and the live scanner.

**The strategy must not be traded live until the validation study
(`python -m ureversal research`) confirms the hypothesis** per the acceptance
criteria in §8.

---

## 2. Notation and data

Let `t` index wall-clock seconds within a trading session.

- `P^S_t`, `P^D_t` — SPY and DIA 1-second bar prices. Bars are reconstructed
  from consolidated trades (volume-weighted close of trades in `[t, t+1)`;
  forward-filled up to `max_ffill_s` seconds when an instrument has no print).
- `x_t = ln P_t` — log price.
- `r_t = x_t − x_{t−1}` — 1-second log return.
- All slopes are expressed in **basis points per second** (bps/s):
  a slope of −0.5 means the instrument is falling at 0.5 bp per second.

Feed: `sip` (consolidated tape, preferred) or `iex` (free tier). The feed is a
config switch; research reports must state which feed produced them.

### 2.1 Rolling regression slope

For window `W` seconds ending at `t`, the OLS slope of log price on time:

```
β_t(W) = Σ_{i=0}^{W−1} (i − ī)(x_{t−W+1+i} − x̄) / Σ (i − ī)²   × 10⁴   [bps/s]
```

### 2.2 Rolling correlation

Pearson correlation of the two instruments' 1-second log returns over window
`W_ρ`:

```
ρ_t(W_ρ) = corr( r^S_{t−W_ρ+1..t} , r^D_{t−W_ρ+1..t} )
```

Seconds where either instrument had no trade (pure forward-fill) are excluded
from the correlation sum; if fewer than `min_corr_obs` valid pairs remain, ρ is
undefined and the downtrend condition fails (this guards against stale-quote
artifacts on sparse feeds such as IEX).

### 2.3 Divergence score

Let `d_t = β^D_t(W_r) − β^S_t(W_r)` (DIA slope minus SPY slope, short window
`W_r`). Standardize by its own rolling dispersion over `W_z` seconds:

```
Z_t = ( d_t − mean(d, W_z) ) / std(d, W_z)
```

`Z_t` measures how unusually fast DIA is accelerating away from SPY relative
to the pair's recent behavior — this is the statistical-significance filter on
the divergence.

---

## 3. State machine

The detector is a four-state machine evaluated once per second. It never looks
forward; every quantity at time `t` uses data up to and including `t`.

```
IDLE ──(mutual downtrend confirmed)──► DOWNTREND
DOWNTREND ──(SPY flattens)──► BOTTOMING
BOTTOMING ──(DIA leading reversal)──► TRIGGER (enter long)
any state ──(condition lapses / window ends)──► IDLE
```

### 3.1 State: DOWNTREND (Step 1 — mutual decline)

Enter DOWNTREND when **all** of the following have held for `D_min`
consecutive seconds (grid: 15, 30, 45):

| Condition | Definition |
|---|---|
| SPY declining | `β^S_t(W_s) < −θ_down` |
| DIA declining | `β^D_t(W_s) < −θ_down` |
| Correlated | `ρ_t(W_ρ) ≥ ρ_min` (grid: 0.60–0.95) |
| Material move | cumulative decline from the rolling `W_hi`-second high: `x^S_max − x^S_t ≥ δ_min` **and** same for DIA |

Record `t_dn` (downtrend confirmation time), `L^S = min x^S` and the running
low as the state persists.

### 3.2 State: BOTTOMING (Step 2 — SPY flattening)

From DOWNTREND, enter BOTTOMING when SPY's decline decelerates:

| Condition | Definition |
|---|---|
| Slope near zero | `β^S_t(W_f) > −θ_flat` (first derivative → 0) |
| Velocity reduction | `|β^S_t(W_f)| ≤ κ · |β̄^S_dn|` where `β̄^S_dn` is SPY's mean slope during the DOWNTREND phase and `κ < 1` |
| Curvature | `β^S_t(W_f) − β^S_{t−W_f}(W_f) > 0` (second derivative positive: decline decelerating — the bottom of a U, not a pause in a slide) |

Deliberately, there is **no** "no new low for N seconds" waiting period:
empirically that wait expires only after SPY has already turned, which defeats
the entire point of a *leading* signal. New lows are handled as a lapse rule
instead: BOTTOMING falls back to IDLE if SPY prints a new episode low below
`L^S − ε_low`, or if `T_bottom_max` seconds pass without a trigger. The
trigger (§3.3) is evaluated from the same second BOTTOMING is entered.

### 3.3 TRIGGER (Step 3 — DIA leading reversal)

From BOTTOMING, fire the entry trigger at the first second where **all** hold:

| Condition | Definition |
|---|---|
| DIA reversing | `β^D_t(W_r) > +θ_up` (positive short-term slope) |
| DIA retracing | DIA has recovered ≥ `ϕ_retrace` of its own decline: `(x^D_t − L^D) / (H^D − L^D) ≥ ϕ_retrace` |
| SPY still flat | `−θ_flat ≤ β^S_t(W_f) ≤ +θ_lead` (SPY has *not* already reversed — otherwise there is nothing left to lead) |
| Significant divergence | `max(Z_{t−W_zl+1..t}) ≥ z_min` — the divergence spike occurs *at* DIA's independent turn and decays within seconds as SPY's own slope normalizes, so the gate looks at the recent maximum over a short lookback `W_zl` rather than the instantaneous value |

`H^D`, `L^D` are DIA's high/low over the episode (from `W_hi` before `t_dn`).

### 3.4 Entry (Step 4)

On TRIGGER at second `t*`:

- **Instrument**: SPY shares (options and ES/MES hooks defined in §9, not armed).
- **Side**: long only.
- **Size**: `floor( equity × alloc_pct / P^S_{t*} )` shares, subject to risk layer §7.
- **Order**: marketable limit at `ask + min(offset_cap, spread)`;
  cancel unconditionally if not filled within `fill_timeout_s` (default 2 s).
  No re-quote chasing: an unfilled entry is a skipped trade.
- Latency model in backtest: signal at close of second `t*`, order eligible to
  fill from `t*+1` at that bar's prices plus half-spread + slippage (§6).

Only in window `[09:33:00, 09:53:00)` ET; entries are further blocked after
`09:51:00` if the minimum exit horizon (time stop) would cross the window end.

---

## 4. Exit logic

Four exit families are backtested independently and in combination. First
condition hit wins. All variants share a **hard exit**: flatten at
`09:54:30 ET` at the latest (positions never survive past the opening window;
this also satisfies "flat before close" trivially).

1. **Fixed target**: limit at entry × (1 + G), G ∈ {0.10%, 0.20%, 0.30%};
   paired protective stop at entry × (1 − S), S optimized.
2. **Trailing stop**:
   - ATR-based: trail = `m_ATR × ATR_{W_atr}(1s bars)` below running high since entry.
   - Percentage: trail = `τ%` below running high since entry.
3. **Momentum failure** — exit at market when any of:
   - DIA momentum lost: `β^D_t(W_r) < 0`
   - SPY stalled: `β^S_t(W_f) < 0` for `N_stall` consecutive seconds after entry
   - Divergence gone: `Z_t < z_exit` (with `z_exit < z_min`)
4. **Time stop**: exit after H ∈ {30, 60, 120, 300} seconds.

---

## 5. Parameters

All parameters live in `ureversal/ureversal.yaml`. Optimization grids are
declared there; the walk-forward optimizer (§8.4) selects values. Defaults
(pre-optimization priors):

| Symbol | Config key | Default | Grid |
|---|---|---|---|
| `W_s` | slope_window_s | 20 | 10, 20, 30 |
| `W_ρ` | corr_window_s | 30 | 20, 30, 60 |
| `W_f` | flat_window_s | 10 | 5, 10, 15 |
| `W_r` | reversal_window_s | 10 | 5, 10, 15 |
| `W_z` | zscore_window_s | 120 | — |
| `W_hi` | episode_high_lookback_s | 120 | — |
| `D_min` | min_downtrend_s | 30 | 15, 30, 45 |
| `ρ_min` | min_correlation | 0.70 | 0.60 … 0.95 step 0.05 |
| `θ_down` | min_down_slope_bps | 0.30 | 0.15, 0.30, 0.50 |
| `δ_min` | min_cum_decline_bps | 8 | 5, 8, 12, 18 |
| `θ_flat` | flat_slope_bps | 0.15 | 0.10, 0.15, 0.25 |
| `κ` | velocity_reduction_ratio | 0.5 | 0.3, 0.5, 0.7 |
| `ε_low` | new_low_tolerance_bps | 1.0 | — |
| `W_zl` | z_lookback_s | 15 | — |
| `T_bottom_max` | max_bottoming_s | 90 | — |
| `θ_up` | min_dia_up_slope_bps | 0.30 | 0.15, 0.30, 0.50 |
| `ϕ_retrace` | min_dia_retrace | 0.10 | 0.05, 0.10, 0.15, 0.25 — kept small: a large retrace requirement is mutually exclusive with "SPY still flat" for a 0.85-correlated pair; sustained-slope `θ_up` over `W_r` carries the bounce protection |
| `θ_lead` | max_spy_lead_slope_bps | 0.15 | — |
| `z_min` | min_divergence_z | 1.5 | 1.0, 1.5, 2.0 |
| `z_exit` | exit_divergence_z | −1.0 | — (0 fires instantly: the trigger spike inflates z's rolling mean, so z dips below 0 mechanically right after entry) |

---

## 6. Cost model

Backtests and the research study apply, per side:

- **Half-spread**: from actual NBBO quotes when available, else configured
  default (SPY 1¢, DIA 2¢ — DIA's book is materially thinner).
- **Slippage**: `slippage_bps` (default 0.5 bp) on top of half-spread.
- **Fees**: commission per share (default $0 Alpaca) + SEC/TAF on sells.
- Fills only against the *next* bar after a signal (no same-second fills).

An edge that does not survive this model **fails** validation regardless of
gross statistics.

---

## 7. Risk layer (hard, strategy-independent)

| Control | Default |
|---|---|
| Max simultaneous positions | 1 |
| Max trades/day | 3 |
| Daily loss limit | 1% of session-start equity → halt for the day |
| Consecutive-loss breaker | 3 consecutive losing days → halt until manual reset |
| Max capital allocation | 25% of equity per trade |
| Kill switch | `./KILL_SWITCH` file or dashboard endpoint; halts new orders, flattens open position |
| PDT guard | if configured equity < $25k: max 3 day-trades per rolling 5 sessions |
| Mode gate | live orders require `LIVE_TRADING_ENABLED=true` env var; default paper |

---

## 8. Validation methodology (must pass before live)

### 8.1 Existence & frequency
Detect all TRIGGER events over ≥ 250 sessions spanning ≥ 2 volatility regimes.
Report events/day and distribution across the window.

### 8.2 Lead-lag: does DIA actually lead SPY?
- Event-level cross-correlation: `corr( r^D_{t−k}, r^S_t )` for lags k ∈ [−10, +10]s
  within episodes; DIA leads iff positive-lag mass significantly exceeds
  negative-lag mass (paired bootstrap over episodes).
- Reversal-timing test: within each U episode, time of DIA slope zero-cross vs
  SPY slope zero-cross; DIA leads iff median Δ > 0 with Wilcoxon p < 0.05.

### 8.3 Edge vs null model
Conditional net forward return after TRIGGER vs two nulls:
1. **Time-of-day matched random entries** (same sessions, same window, no signal).
2. **Signal on shuffled pairing** (SPY signal computed against DIA from a
   different session — destroys the intermarket link, preserves both marginals).

Pass requires: mean net expectancy > 0 with bootstrap 95% CI excluding 0,
**and** expectancy exceeding both nulls' 95th percentile.

### 8.4 Walk-forward optimization
Expanding-window: optimize on months `1..k`, test on month `k+1`, roll. Report
out-of-sample-only: win rate, profit factor, Sharpe (per-trade, annualized on
trade frequency), max drawdown, expectancy, average hold time. Parameter
stability across folds is itself a pass criterion (a knife-edge optimum fails).

### 8.5 Regime robustness
Split results by VIX tercile and by trend day / range day; edge must not be
concentrated in a vanished regime.

---

## 9. Execution architecture hooks (not armed)

`ExecutionVenue` is an interface. Implemented: `AlpacaEquityVenue` (SPY
shares, paper + live). Declared stubs with the same order contract:
`SpyOptionsVenue` (0DTE calls, delta-targeted), `FuturesVenue` (ES/MES via a
futures-capable broker; Alpaca does not offer futures). Switching venue is a
config change once a venue is implemented and separately validated.

---

## 10. Known threats to validity (tested, not assumed away)

1. **Membership overlap**: all DOW 30 names are inside SPY (~27% by weight);
   apparent lead can be mechanical. The shuffled-pair null (§8.3) addresses this.
2. **Stale prints on thin DIA**: DIA trades ~50× less than SPY; a "reversal"
   may be bid-ask bounce. Correlation uses trade-time filtering (§2.2);
   trigger requires retrace `ϕ` of range, not a single print.
3. **IEX feed sparsity**: on the free feed both effects worsen; reports carry
   the feed label and the study is re-run on SIP before any live decision.
4. **Multiple-testing bias**: grids are modest and walk-forward OOS-only
   metrics are the ones that count.
5. **Open auction effects**: window starts 09:33, after the primary open
   print and the worst of the auction imbalance unwind.
