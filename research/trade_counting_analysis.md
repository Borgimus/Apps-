# Trade Counting Semantics Analysis

*Research only — no code changes. Generated 2026-06-01.*

---

## 1. What Increments `max_trades_per_day`

`risk.record_trade()` is called in **three places**:

| # | File | Line | Event | Timing |
|---|---|---|---|---|
| + | `scripts/session_runner.py` | 971 | Entry order placed with broker | Immediately after broker.place_option_order() returns a non-None order object |
| + | `scripts/session_runner.py` | 243 | Position exit (trailing_stop / stop_loss / take_profit) | Inside position_monitor(), when pm.should_exit() returns a reason |
| + | `scripts/session_runner.py` | 388 | EOD forced liquidation exit | Inside eod_liquidate(), when positions are force-closed at session end |

**Does NOT increment:**

- Fill confirmation by FillTracker.poll() — the risk parameter is passed to _handle_fill() but never used
- Partial fills
- Order cancellation or rejection
- Risk-gate rejection (check_order failure)
- Dedup / cooldown skips

### Docstring Discrepancy

```python
def record_trade(self, pnl: Decimal = Decimal("0")):
    """Call when an order is filled to track daily counters."""
    self._trades_today += 1
```

The docstring says **"when an order is filled"** but:
- Entry calls happen at **order placement** (line 971), before fill confirmation from the broker.
  An entry order that is placed but later cancelled (never filled) still consumes a slot.
- FillTracker.`_handle_fill()` receives `risk` as a parameter but **never calls `record_trade()`**.

### `check_order` Scope

`check_order()` is called **exactly once** in `session_runner.py` (line 854) — for **entry orders only**.
Exit orders (trailing_stop, stop_loss, EOD) bypass `check_order()` entirely.
They always succeed, but they still increment the counter, eating into available entry slots.

---

## 2. Trade Lifecycle Count — 2026-05-29

Starting counter: **0**

### Complete Timeline

| Time (ET) | Event | record_trade()? | Counter |
|---|---|:---:|:---:|
| 10:34:01.775946 | SNOW VWAP entry order placed with broker | **YES** | **1** |
| 10:35:36.312081 | SNOW fill confirmed by FillTracker.poll() | — | 1 |
| 10:37:07.422310 | SNOW trailing_stop exit triggered by position_monitor() | **YES** | **2** |
| 11:04:19.486139 | Scan cycle: MSFT VWAP signal processed FIRST (score=60, higher rank in scan) | **YES** | **3** |
| 11:04:19.486139 | DIA ORB check_order() in same scan cycle after MSFT placed | — | 3 |
| 11:04:19.486139 | DIA VWAP check_order() in same scan cycle | — | 3 |
| 11:04:53.462087 | Next scan cycle: DIA ORB and DIA VWAP checked again | — | 3 |
| 11:04:53.546112 | MSFT fill confirmed by FillTracker.poll() | — | 3 |
| 11:05:24.751724 | Next scan cycle: DIA VWAP blocked (DIA ORB not evaluated here) | — | 3 |
| 11:05:55.445879 | MSFT trailing_stop exit triggered by position_monitor() | **YES** | **4** |
| 11:05:55 to 11:20:34 | DIA signals (ORB and VWAP) enter 15-minute cooldown_after_loss | — | 4 |
| 11:21:04.309265 | Cooldown expires. DIA ORB and DIA VWAP return to risk gate. | — | 4 |
| 12:00+ to 12:30 | MSTR ORB signals appear (49 bridge rows) | — | 4 |

### Summary: Why All ORB Signals Were Blocked

| Metric | Value |
|---|---|
| Total ORB blocks | 77 |
| Blocked at "3/3" | 2 |
| Blocked at "4/3" | 75 |
| First ORB block | 11:04:19.486139 |
| Last ORB block | 12:29:55.208847 |
| ORB signals reaching merit evaluation | **0** |

**Key sequence:**

1. SNOW entry at 10:34:01 → counter=1
2. SNOW exit at 10:37:07 (trailing_stop, hold 91s) → counter=2
3. MSFT entry at 11:04:19 → counter=3 (risk check before placement: 2<3 ✓)
4. DIA ORB and DIA VWAP in the same scan cycle: check_order sees counter=3 → **"3/3 reached"** (5 blocks, 11:04–11:05)
5. MSFT exit at 11:05:55 (trailing_stop, hold 62s) → counter=4
6. MSFT loss triggers 15-minute `cooldown_after_loss` timer for DIA signals
7. 11:05:55–11:20:34: DIA skipped as `cooldown_after_loss` (never reaches risk gate)
8. 11:21:04: cooldown expires; DIA returns to risk gate → counter=4 → **"4/3 reached"** (149 blocks, 11:21–12:30)
9. 12:00–12:30: MSTR ORB signals arrive → immediately blocked "4/3" (49 blocks)

---

## 3. Effective Session Capacity

**Formula (interleaved entry/exit pattern):**
Each round-trip costs 2 counter units (1 entry placement + 1 exit closure).
An entry is blocked when `counter >= max`. Exits are never blocked.

`max_entries = ceil(max_trades_per_day / 2)`

### Capacity Table

| `max_trades_per_day` | Max Entry Orders | Max Complete Round-Trips | Counter at Limit |
|---:|---:|---:|---:|
| 3 | 2 | 2 | 4 |
| 5 | 3 | 3 | 6 |
| 10 | 5 | 5 | 10 |

### Sequence Traces

**max=3 (current config):**
```
E→1  [check:0<3✓]  X→2  E→3 [check:2<3✓]  X→4  E→[check:4>=3 BLOCKED]
= 2 entries, 2 exits, then locked
```

**max=5:**
```
E→1  X→2  E→3 [check:2<5✓]  X→4  E→5 [check:4<5✓]  X→6  E→[check:6>=5 BLOCKED]
= 3 entries, 3 exits, then locked
```

**max=10:**
```
E→1 X→2 E→3 X→4 E→5 X→6 E→7 X→8 E→9 [check:8<10✓] X→10 E→[check:10>=10 BLOCKED]
= 5 entries, 5 exits, then locked
```

> **Note:** `max_active_positions=2` is a parallel constraint that limits concurrent open positions 
> regardless of the trade counter. Under max=3, both constraints were binding on 2026-05-29: 
> SNOW+MSFT exhausted both the concurrent slot limit and the trade counter simultaneously.

---

## 4. Interpretation Comparison

### Definitions

| Label | Name | Rule |
|---|---|---|
| **A_current** | Current: count entries (at placement) + count exits | record_trade() called on: entry order placement + position exit closure |
| **B_round_trip** | Alternative: count completed round-trip positions | record_trade() would be called only when a position is fully closed (entry+exit pair) |
| **C_entry_only** | Alternative: count entry orders only | record_trade() would be called only on entry order placement |

### 2026-05-29 Session Under Each Interpretation

| Metric | A: Current | B: Round-Trip | C: Entry-Only |
|---|---|---|---|
| Trades executed | 2 | 2 | 2 |
| Counter at session end | 4 | 2 | 2 |
| ORB signals blocked | 77 | 0 | 0 |
| First ORB opportunity | Never | 11:04:19 (DIA q=3, sc=53) | 11:04:19 (DIA q=3, sc=53) |

### Risk Exposure Difference

| Comparison | Max Concurrent Positions | Max Concurrent Premium Risk |
|---|---|---|
| A vs B | Identical (same 2 trades taken) | Identical |
| A vs C | A=2 entries, C=3 possible entries | C has 1 additional ORB entry possible |

> **Key finding:** Under interpretations B and C, `max_active_positions=2` remains the binding 
> constraint on simultaneous exposure. The additional ORB entry under B or C would only occur 
> after one of the two VWAP positions was already closed — so max concurrent exposure is unchanged.

### Detailed Scenario: What Would Have Happened Under Interpretation B or C on 2026-05-29

1. SNOW entry (10:34) → counter=1
2. SNOW exit (10:37) → counter=2 (B) or counter=1 (C)
3. MSFT entry (11:04) → counter=3 (B) or counter=2 (C)
4. DIA ORB check (11:04 same scan cycle):
   - **B:** counter=3 >= 3 → still blocked (MSFT is open, round-trip not yet complete)
   - **C:** counter=2 < 3 → **DIA ORB evaluated on merit**. Quality=3, scanner=53, spread 1.77%.
5. MSFT exit (11:05) → counter=2 (B: round-trip complete) or still 2 (C: unchanged)
6. DIA ORB cooldown: 11:05:55–11:21:04 (15 min, regardless of counting model)
7. DIA ORB returns at 11:21:04:
   - **B:** counter=2 < 3 → **evaluated on merit**
   - **C:** counter=2 < 3 → **evaluated on merit**
8. MSTR ORB at 12:04:
   - **B:** counter=2 (or 3 if DIA ORB round-trip completed) → **evaluated on merit**
   - **C:** counter=2 (or 3 if DIA ORB also entered) → depends on prior entries

---

## 5. Design Intent Assessment

### Evidence Summary

| Source | Evidence | Points to |
|---|---|---|
| `record_trade()` docstring | "Call when an order is **filled** to track daily counters." | Entry-at-fill or round-trip |
| Counter name | `_trades_today` | "A trade" = 1 directional transaction, not 2 |
| Config name | `max_trades_per_day` | Round-trip or entry-only; industry standard is entries |
| Peer constraints | `max_risk_per_trade`, `max_daily_loss` — both entry-level | Symmetry suggests entry-only |
| Test (line 113-123) | Calls `record_trade()` 3 times generically, no entry/exit distinction | Ambiguous |
| `check_order` scope | Called once, at entry only — exits are unchecked | Entry is the "trade" event |

### Assessment

The most defensible design intent, based on the docstring, naming conventions, and constraint symmetry, is:

> **`max_trades_per_day` was intended to limit the number of entry orders (one-sided entries) per session,
> counted at fill confirmation.**

The current implementation diverges from this intent in two ways:

| Divergence | Intended Behavior | Current Behavior |
|---|---|---|
| Entry counting trigger | At fill confirmation | At order placement (before fill) |
| Exit counting | Not counted | Counted — each exit consumes a slot |

### Practical Impact on 2026-05-29

| Scenario | ORB DIA evaluated? | ORB MSTR evaluated? |
|---|:---:|:---:|
| Current behavior | NO (blocked 11:04) | NO (blocked 12:04) |
| Intended behavior (entry-only-at-fill) | YES (counter=2 at 11:04) | YES (counter=2 at 12:04) |
| Round-trip counting | YES (after MSFT exit, 11:21+) | YES (counter=2 at 12:04) |

Under either alternative interpretation, **at least one ORB signal window would have been accessible** 
on 2026-05-29, assuming the ORB slot reservation feature had also not yet been active.

---

## Summary

| Question | Answer |
|---|---|
| What increments the counter? | Entry order placement + position exit closure (both sides of each trade) |
| What does NOT increment? | Fill confirmations, partial fills, cancellations, rejections, skips |
| Effective entries with max=3 | 2 (not 3) |
| Effective entries with max=5 | 3 (not 5) |
| Effective entries with max=10 | 5 (not 10) |
| Were ORB signals evaluated on merit? | No — all 77 ORB blocks were pure risk-counter blocks |
| Docstring alignment? | No — docstring says "at fill" but entry is counted at placement |
| Most likely design intent | Entry-only counting (possibly at fill) — exits were not intended to consume slots |

---

## Historical Note — Fix Applied 2026-06-01

The semantics described in this document (exits consuming entry capacity) were corrected in commit 
on branch `claude/options-trading-research-system-TIU0p`.

**Changes made:**
- `RiskManager._trades_today` replaced with three counters: `_entries_today`, `_pending_entries`, `_exits_today`
- New methods: `record_entry_pending()`, `record_entry_filled()`, `record_entry_cancelled()`, `record_exit(pnl)`
- `record_trade()` kept as deprecated backward-compat alias
- `_check_max_trades_per_day()` uses `entries_today + pending_entries >= max_trades_per_day`
- `session_runner.py` exit paths changed from `record_trade(pnl)` → `record_exit(pnl)`
- `session_runner.py` entry placement changed from `record_trade()` → `record_entry_pending()`
- `FillTracker._handle_fill()` now calls `risk.record_entry_filled()` on confirmed fills
- `FillTracker._handle_dead()` now calls `risk.record_entry_cancelled()` on cancel/reject/expire

**Effective capacity after fix:**
With `max_trades_per_day=3`: **3 entries** (not 2 as before).
Each entry reservation is freed either by a fill confirmation (promoting to `entries_today`)
or by a cancellation (decrementing `pending_entries`).

**What the 2026-05-29 session would have looked like under the fixed semantics:**
- SNOW entry(10:34)→entries_today=1, SNOW exit→exits_today=1 (no counter effect on entries)
- MSFT entry(11:04)→entries_today=2
- MSFT exit→exits_today=2 (no counter effect on entries)
- ORB signals from 12:04+ → entries_today=2, capacity_used=2 < 3 → **ALLOWED**
- One ORB trade could have been placed before the session ended

This confirms the original blocking of ORB was entirely an artifact of the counter bug, not a
reflection of ORB's actual signal quality or strategy viability.
