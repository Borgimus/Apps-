# Post-Session Report — S18 (2026-07-10)

**Session window:** 10:36–12:30 ET (late start — first 66 min missed)
**Cycles:** 220
**Validity:** QUESTIONABLE — VM teardown at ~09:05 ET caused 66-min late start. User must rule.
**P&L:** -$53.00 (corrected post-session — see MARA note below)
**Trades:** 3 (0 wins, 3 losses)
**data_clean:** FALSE

---

## Infrastructure

VM torn down ~09:05 ET (stop hook commit ended the turn; user idle >13 min → Firecracker teardown).

Recovery sequence:
1. `git fetch origin` — confirmed all commits on GitHub (49dd8a0 latest)
2. `git reset --hard origin/claude/options-trading-research-system-TIU0p` — restored all files
3. `pip install -r requirements.txt` — restored Python deps (fresh VM had none)
4. Session relaunched as background task `b0oeuin7a` at 10:36 ET

First scan at 10:35:57 ET. EOD exit at 12:30:34 ET. 220 cycles.

---

## Trades

| # | Symbol | Contract | Strategy | Entry | Exit | Reason | Hold | P&L |
|---|--------|----------|----------|-------|------|--------|------|-----|
| 1 | SOFI | SOFI260710P00019000 | ORB | $0.30 | $0.18 | trailing_stop | 154s | -$12 |
| 2 | SPY | SPY260710P00746000 | vwap_reclaim | $0.24* | $0.14 | trailing_stop | 62s | -$10 |
| 3 | MARA | MARA260710P00014000 | vwap_reclaim | $1.49* | **$1.18**† | post_session_close | 3197s | **-$31** |

**Total: -$53.00**

*SPY limit $0.27, filled $0.24. MARA limit $1.51, filled $1.49 (both better fills).

†MARA: Session EOD exit limit $1.39 placed at 12:30:35 ET did not fill (market dropped to $1.16-$1.20 range). Stale order cancelled post-session; new limit sell $1.16 placed, filled at **$1.18** (Alpaca confirmed). Actual loss: -$31.

All 3 positions were DTE=0 (same-day expiry). All 3 were short (put buyers).

---

## Trade Details

**Trade 1 — SOFI (ORB, -$12):**
- Scanner: score=50, signal=SHORT (atr_wide 5.02%, orb_breakdown)
- Contract: OI=3168, vol=12431, spread=3.39%
- Entry: $0.30 @ 10:36 ET, fill in 33s
- Exit: trailing_stop @ $0.18, 10:39 ET (hold 154s)
- Exit spread: 28.6% (bid=$0.18, ask=$0.24) — WARNING, proceeded

**Trade 2 — SPY (vwap_reclaim, -$10):**
- Contract: SPY260710P00746000, OI=5133, vol=29657, spread=3.8%
- Entry: limit $0.27, filled $0.24 @ 11:06 ET, fill in 45s
- Exit: trailing_stop @ $0.14, 11:07 ET (hold 62s — 1 min, immediate reversal)

**Trade 3 — MARA (vwap_reclaim, -$31):**
- Scanner: score=62, signal=SHORT (atr_wide 10.95%, orb_breakdown, trend_sideways)
- Contract: OI=5150, vol=795, spread=9.0% (at entry)
- Entry: limit $1.51, filled $1.49 @ ~11:37 ET, fill in 48s
- Session exit attempt: EOD limit $1.39 placed 12:30:35 ET — DID NOT FILL (market dropped to $1.16 bid)
- Post-session close: stale order cancelled; new limit $1.16 placed ~12:45 ET, filled at **$1.18** (Alpaca confirmed)
- Actual P&L: ($1.18 - $1.49) × 100 = -$31

---

## Scanner

- Symbols scanned: 152
- Passed scanner: 10 active symbols across session (SOFI, MARA, NVDA, HOOD at various points)
- Rejected: 142 (most: low_volume_chop mid-morning)
- Max entries reached (3/3) — HOOD rejected repeatedly in final hour
- 42 risk rejections (all "Max entries per day reached: 3/3")

---

## Data Integrity

**MARA: EOD exit failed — closed post-session at $1.18 (confirmed):**
- Session EOD limit sell $1.39 placed 12:30:35 ET — market had fallen to bid $1.16, order never filled
- Post-session: stale order 88d61d07 cancelled; new limit sell $1.16 placed (order 2862a874); Alpaca filled at **$1.18**
- Corrected P&L for MARA: ($1.18-$1.49)×100 = **-$31** (session journal recorded -$10 at the unexecuted $1.39 price)
- S18 total corrected: **-$53** (was -$32 in session journal)

**Exit spread warnings (during session):**
- SOFI: 28.6% at trailing_stop exit (bid=$0.18, ask=$0.24 — proceeded)
- MARA: 10.9% at EOD exit attempt (bid=$1.39, ask=$1.55 — order placed but did not fill)

**api_errors:** 0
**reconciler_warnings:** 0

---

## Phase 2 Status

| Session | Date | P&L | Validity |
|---------|------|-----|----------|
| S6 | 2026-06-15 | +$35 | valid |
| S7 | 2026-06-17 | $0 | valid |
| S8 | 2026-06-18 | -$7 | valid |
| S9 | 2026-06-24 | $0 | valid |
| S10 | 2026-06-25 | $0 | valid |
| S11 | 2026-06-29 | -$7 | valid |
| S16 | 2026-07-08 | $0 | valid |
| S17 | 2026-07-09 | +$434 | valid |
| S18 | 2026-07-10 | -$32 | **questionable** |

**Running Phase 2 P&L (valid sessions):** +$455
**If S18 counted:** +$423
**Remaining:** 1 session (if S18 valid) or 2 sessions (if voided)

---

## Observations

1. **Short hold times on losing trades**: SOFI (154s) and SPY (62s) both hit trailing stops within 2-3 minutes of entry. The options moved against position immediately — classic DTE=0 gamma risk. Small underlying moves = large option % swings in either direction.

2. **MARA held 53 minutes**: Only position that had time to develop a thesis. Entry at 11:37 ET, option stayed near entry until EOD forced exit at -$10. Low vol=795 is a concern for this contract.

3. **All 3 exits by trailing/EOD — no take_profit or stop_loss**: stop_loss requires -50% loss ($0.15, $0.12, $0.745) which was not hit. take_profit requires +100% which was not hit. Trailing_stop (peak×0.75) caught SOFI and SPY before the hard stops.

4. **Max entries/day hit**: Risk manager capped at 3 entries; HOOD was repeatedly selected but blocked in final hour.
