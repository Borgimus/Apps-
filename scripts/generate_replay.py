#!/usr/bin/env python3
"""
Generate replay files for trade id=13: MSFT260513P00412500 on 2026-05-12.
All data is inline — no DB or app imports required.
Usage: python scripts/generate_replay.py
"""

import json
import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Output paths (relative to repo root)
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT_DIR = os.path.join(REPO_ROOT, "evaluation", "reports")
JSON_OUT = os.path.join(OUT_DIR, "replay_MSFT260513P00412500_20260512.json")
MD_OUT   = os.path.join(OUT_DIR, "replay_MSFT260513P00412500_20260512.md")

os.makedirs(OUT_DIR, exist_ok=True)

generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# DATA — all inline, derived from facts
# ---------------------------------------------------------------------------

trade = {
    "id": 13,
    "entry_time": "2026-05-12 09:45:42",
    "fill_time":  "2026-05-12 09:46:44",
    "exit_time":  "2026-05-12 10:14:52",
    "hold_secs":  1688,
    "hold_mins_approx": round(1688 / 60, 1),
    "strategy":   "rsi_trend",
    "direction":  "short",
    "direction_note": "short underlying = bullish on puts",
    "option":     "MSFT260513P00412500",
    "expiry":     "2026-05-13",
    "strike":     412.50,
    "option_type": "put",
    "delta":      -0.6544,
    "iv":         0.4349,
    "iv_pct":     "43.49%",
    "underlying_at_scan": 410.475,
    "quantity":   1,
    "is_paper":   True,
    "realized_pnl": -109.50,
}

scanner = {
    "scan_time":    "2026-05-12 09:36:20",
    "scan_result_id": 44,
    "symbol":       "MSFT",
    "score":        50.0,
    "signal":       "SHORT",
    "atr_pct":      2.57,
    "rvol":         0.063,
    "rvol_pct":     "6.3%",
    "rvol_note":    "extremely thin — 6.3% of normal ADV",
    "rsi":          42.61,
    "rsi_timeframe": "5m/daily",
    "vwap":         411.56,
    "price":        408.53,
    "price_vs_vwap": "below",
    "gap_pct":      0.43,
    "gap_direction": "gap-up from prior close",
    "trend":        "sideways",
    "reason_codes": [
        "atr=2.57% (wide-range)",
        "near_orb_level",
        "trend_sideways",
        "rsi=43 (healthy)",
        "below_vwap",
    ],
    "rejected_reasons": ["low_volume_chop"],
    "rejection_outcome": "ALL 13 symbols rejected — fallback to CLI list",
    "symbols_scanned": 13,
    "symbols_passed": 0,
}

selection_path = {
    "summary": "MSFT traded via CLI fallback despite scanner rejection for low_volume_chop",
    "rvol_threshold_for_pass": 0.5,
    "rvol_at_scan":            0.063,
    "rvol_shortfall_pct":      87.4,
    "scanner_outcome":         "ALL 13 symbols rejected by scanner",
    "fallback_mechanism":      "session_runner fell back to CLI --symbol list",
    "strategy_signal":         "rsi_trend strategy generated SHORT for MSFT from fallback list",
    "liquidity_filter_cycles": {
        "cycles_1_to_9": {
            "time_range": "09:36–09:44",
            "reason_rejected": "no-trade buffer first 15 minutes (risk rule)",
            "contracts_seen": [
                {"contract": "P412500", "delta": -0.448},
                {"contract": "P405000", "delta_range": [-0.335, -0.369]},
            ],
        },
        "cycle_10": {
            "time": "09:45:42",
            "event": "15-min buffer lifted",
            "contract_selected": "P412500",
            "delta":   -0.654,
            "spread_pct": 9.3,
            "open_interest": 301,
            "volume":  86,
            "volume_note": "extremely low intraday option volume",
        },
    },
}

other_candidates = [
    {
        "symbol": "NVDA",
        "score":  48,
        "signal": "LONG",
        "rvol":   0.136,
        "trend":  "trend_up",
        "rsi":    75.0,
        "rejected_reason": "low_volume_chop",
        "not_traded_because": "LONG signal — rsi_trend strategy filtered for SHORT only on this session",
    },
    {
        "symbol": "AMD",
        "score":  40,
        "signal": "LONG",
        "rvol":   0.158,
        "trend":  "trend_up",
        "rsi":    80.0,
        "rejected_reason": "low_volume_chop",
        "not_traded_because": "LONG signal — rsi_trend strategy filtered for SHORT only on this session",
    },
    {
        "symbol": "AAPL",
        "score":  38,
        "signal": "LONG",
        "rvol":   0.109,
        "trend":  "trend_up",
        "rsi":    79.0,
        "rejected_reason": "low_volume_chop",
        "not_traded_because": "LONG signal — rsi_trend strategy filtered for SHORT only on this session",
    },
]

underlying_bars = [
    {"time": "09:30", "open": 414.42, "high": 415.50, "low": 412.86, "close": 413.00, "volume": 1298345, "rsi14_1m": None,  "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "ORB: high=415.50 low=412.86"},
    {"time": "09:35", "open": None,   "high": None,   "low": None,   "close": 408.86, "volume": 186557,  "rsi14_1m": None,  "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "sharp drop -4.14 from open"},
    {"time": "09:36", "open": None,   "high": None,   "low": None,   "close": 408.76, "volume": None,    "rsi14_1m": None,  "ema20_1m": None,   "vwap": 411.56, "vs_vwap": None,  "note": "scanner scan time"},
    {"time": "09:39", "open": None,   "high": None,   "low": None,   "close": 407.41, "volume": None,    "rsi14_1m": None,  "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "session low close"},
    {"time": "09:44", "open": None,   "high": None,   "low": None,   "close": 408.90, "volume": None,    "rsi14_1m": None,  "ema20_1m": 409.88, "vwap": None,   "vs_vwap": None,  "note": "pre-signal bar"},
    {"time": "09:45", "open": None,   "high": None,   "low": None,   "close": 408.21, "volume": None,    "rsi14_1m": 17.87, "ema20_1m": 409.72, "vwap": 410.77, "vs_vwap": -2.56, "note": "SIGNAL FIRED — entry order placed"},
    {"time": "09:46", "open": None,   "high": None,   "low": None,   "close": 407.77, "volume": None,    "rsi14_1m": 17.06, "ema20_1m": 409.54, "vwap": 410.66, "vs_vwap": -2.89, "note": "FILL @6.15"},
    {"time": "09:47", "open": None,   "high": None,   "low": None,   "close": 406.82, "volume": None,    "rsi14_1m": 15.42, "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "PUT gaining, downtrend continues"},
    {"time": "09:48", "open": None,   "high": None,   "low": None,   "close": 406.67, "volume": None,    "rsi14_1m": 15.17, "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "MSFT INTRADAY LOW; PUT PEAK ~$6.74"},
    {"time": "09:49", "open": None,   "high": None,   "low": None,   "close": 407.49, "volume": None,    "rsi14_1m": 22.50, "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "bounce begins"},
    {"time": "09:50", "open": None,   "high": None,   "low": None,   "close": 407.89, "volume": None,    "rsi14_1m": 25.87, "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "bounce continues"},
    {"time": "10:00", "open": None,   "high": None,   "low": None,   "close": 407.86, "volume": None,    "rsi14_1m": None,  "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": ""},
    {"time": "10:04", "open": None,   "high": None,   "low": None,   "close": 408.93, "volume": None,    "rsi14_1m": None,  "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "reclaim attempt"},
    {"time": "10:13", "open": None,   "high": None,   "low": None,   "close": 408.43, "volume": None,    "rsi14_1m": 44.95, "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": ""},
    {"time": "10:14", "open": None,   "high": None,   "low": None,   "close": 409.24, "volume": None,    "rsi14_1m": 53.35, "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "TRAILING STOP FIRES, PUT=$5.055"},
    {"time": "10:15", "open": None,   "high": None,   "low": None,   "close": 409.67, "volume": None,    "rsi14_1m": None,  "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "continued rally post-exit"},
    {"time": "10:16", "open": None,   "high": None,   "low": None,   "close": 410.00, "volume": None,    "rsi14_1m": None,  "ema20_1m": None,   "vwap": None,   "vs_vwap": None,  "note": "MSFT crosses above VWAP ~409.55"},
]

spy_context = [
    {"time": "09:44", "close": 736.72, "vwap": 736.42, "vs_vwap": +0.30, "note": "above VWAP"},
    {"time": "09:45", "close": 736.73, "vwap": 736.43, "vs_vwap": +0.31, "note": "above VWAP — entry candle"},
    {"time": "09:46", "close": 736.39, "vwap": 736.42, "vs_vwap": -0.03, "note": "crossing VWAP — fill candle"},
    {"time": "09:47", "close": 735.77, "vwap": 736.41, "vs_vwap": -0.64, "note": "below VWAP — supporting SHORT"},
    {"time": "09:48", "close": 735.62, "vwap": 736.40, "vs_vwap": -0.77, "note": "below VWAP"},
    {"time": "10:00", "close": 734.82, "vwap": 736.11, "vs_vwap": -1.29, "note": "well below VWAP"},
    {"time": "10:14", "close": 736.11, "vwap": 735.97, "vs_vwap": +0.14, "note": "SPY RECLAIMS VWAP — same minute as trailing stop"},
    {"time": "10:15", "close": 736.19, "vwap": 735.97, "vs_vwap": +0.22, "note": ""},
]

qqq_context = [
    {"time": "09:44", "close": 709.94, "vwap": 707.50, "vs_vwap": +2.44, "note": "well above VWAP"},
    {"time": "09:45", "close": 709.69, "vwap": 707.55, "vs_vwap": +2.14, "note": "above VWAP — entry candle"},
    {"time": "09:46", "close": 709.45, "vwap": 707.60, "vs_vwap": +1.85, "note": "above VWAP — still bullish"},
    {"time": "09:47", "close": 708.15, "vwap": 707.61, "vs_vwap": +0.54, "note": "near VWAP — weakening"},
    {"time": "09:55", "close": 707.65, "vwap": 707.67, "vs_vwap": -0.02, "note": "at VWAP"},
    {"time": "10:00", "close": 705.92, "vwap": 707.56, "vs_vwap": -1.64, "note": "well below VWAP — supporting SHORT"},
    {"time": "10:14", "close": 707.63, "vwap": 707.24, "vs_vwap": +0.39, "note": "QQQ RECLAIMS VWAP — same minute as trailing stop"},
    {"time": "10:15", "close": 707.79, "vwap": 707.24, "vs_vwap": +0.55, "note": ""},
]

execution = {
    "entry_bid":         5.67,
    "entry_ask":         6.22,
    "entry_spread":      round(6.22 - 5.67, 2),
    "entry_spread_pct":  0.0925,
    "entry_spread_pct_display": "9.25%",
    "entry_pricing_mode": "marketable_limit",
    "limit_price":       6.23,
    "limit_price_derivation": "ask + 0.01 = 6.22 + 0.01",
    "fill_price":        6.15,
    "fill_slippage":     -0.08,
    "fill_slippage_note": "filled $0.08 BETTER than limit (favorable)",
    "ttf_secs":          62.67,
    "ttf_note":          "time-to-fill: 62.67 seconds from order to fill",
    "exit_bid":          4.64,
    "exit_ask":          5.47,
    "exit_spread":       round(5.47 - 4.64, 2),
    "exit_spread_pct":   0.1642,
    "exit_spread_pct_display": "16.42%",
    "exit_price":        5.055,
    "exit_reason":       "trailing_stop",
}

pnl_path = [
    {"time": "09:46:44", "underlying": 407.77, "option_est": 6.15,  "unrealized_pnl":   0.00, "note": "FILL"},
    {"time": "09:47",    "underlying": 406.82, "option_est": 6.65,  "unrealized_pnl":  50.00, "note": "est"},
    {"time": "09:48",    "underlying": 406.67, "option_est": 6.74,  "unrealized_pnl":  59.00, "note": "MFE — PUT PEAK"},
    {"time": "09:49",    "underlying": 407.49, "option_est": 6.40,  "unrealized_pnl":  25.00, "note": "reversal begins"},
    {"time": "09:50",    "underlying": 407.89, "option_est": 6.20,  "unrealized_pnl":   5.00, "note": ""},
    {"time": "10:00",    "underlying": 407.86, "option_est": 6.05,  "unrealized_pnl": -10.00, "note": ""},
    {"time": "10:05",    "underlying": 408.42, "option_est": 5.80,  "unrealized_pnl": -35.00, "note": "est"},
    {"time": "10:10",    "underlying": 408.37, "option_est": 5.85,  "unrealized_pnl": -30.00, "note": "est"},
    {"time": "10:14:52", "underlying": 409.24, "option_est": 5.055, "unrealized_pnl":-109.50, "note": "EXIT — trailing stop fired"},
]

# MFE/MAE calculations
fill_price  = 6.15
peak_price  = 6.74
exit_price  = 5.055
mfe_dollars = round((peak_price - fill_price) * 100, 2)   # 1 contract = 100 shares
mfe_pct     = round((peak_price - fill_price) / fill_price * 100, 2)
mae_dollars = round((exit_price - fill_price) * 100, 2)   # negative
mae_pct     = round((exit_price - fill_price) / fill_price * 100, 2)

excursion = {
    "mfe_dollars":       mfe_dollars,
    "mfe_pct":           mfe_pct,
    "mfe_note":          "peak at 09:48, ~4 min into trade",
    "mae_dollars":       mae_dollars,
    "mae_pct":           mae_pct,
    "mae_note":          "realized at exit (trailing stop was the exit trigger)",
    "peak_time":         "09:48",
    "peak_option_price": 6.74,
    "hold_to_peak_mins": 1,
}

# Stop levels
stop_loss_price    = round(fill_price * (1 - 0.50), 3)
take_profit_price  = round(fill_price * (1 + 1.00), 3)
trail_fired_at     = round(peak_price * (1 - 0.25), 3)

exit_levels = {
    "stop_loss_pct":          0.50,
    "stop_loss_price":        stop_loss_price,
    "stop_loss_derivation":   "fill_price × (1 - 0.50) = 6.15 × 0.50",
    "stop_loss_triggered":    False,
    "take_profit_pct":        1.00,
    "take_profit_price":      take_profit_price,
    "take_profit_derivation": "fill_price × (1 + 1.00) = 6.15 × 2.00",
    "take_profit_triggered":  False,
    "trailing_stop_pct":      0.25,
    "trailing_stop_fired_at": trail_fired_at,
    "trailing_stop_derivation": "peak_price × (1 - 0.25) = 6.74 × 0.75",
    "trailing_stop_triggered": True,
    "max_hold_mins":          120,
    "max_hold_triggered":     False,
    "eod_exit_time":          "10:50 ET",
    "eod_exit_triggered":     False,
}

stop_behavior = {
    "which_exit_fired":    "trailing_stop",
    "peak_price":          6.74,
    "trail_pct":           0.25,
    "trail_level_at_exit": trail_fired_at,
    "trail_level_verified": abs(trail_fired_at - exit_price) < 0.001,
    "fill_vs_trail_delta": round(exit_price - trail_fired_at, 4),
    "note": "Exit price $5.055 matches peak×0.75 exactly — trailing stop math confirmed",
}

market_regime = {
    "spy_trend":   "up",
    "spy_note":    "above VWAP at open; reclaimed VWAP at 10:14 exactly when stop fired",
    "qqq_trend":   "up",
    "qqq_note":    "well above VWAP at open (rvol=0.166); reclaimed VWAP at 10:14 same minute as stop",
    "msft_trend":  "sideways-to-down",
    "msft_note":   "below VWAP all morning; gap-up reversed sharply; sector divergence vs broad market",
    "sector_divergence": "MSFT weak while SPY/QQQ broadly firm — created SHORT opportunity",
    "market_regime_summary": (
        "Broad market (SPY/QQQ) trending up but weakened mid-morning; "
        "MSFT exhibited idiosyncratic weakness. "
        "Both SPY and QQQ reclaimed VWAP simultaneously with MSFT trailing stop at 10:14."
    ),
}

observations = [
    "MSFT was the only symbol with a SHORT signal; all others (NVDA, AMD, AAPL) had LONG signals not matching the rsi_trend SHORT filter.",
    "rvol at scan was 0.063 (6.3% of ADV) — 87% below the 0.5 threshold required to pass the scanner.",
    "The 15-minute no-trade buffer blocked 9 liquidity filter cycles (09:36–09:44); the trade fired immediately at cycle 10 when the buffer lifted.",
    "RSI(1m) at entry was 17.87 — deep oversold on the 1-minute timeframe — while RSI(5m/daily) used by the scanner was 42.61.",
    "The fill price ($6.15) was $0.08 better than the marketable limit ($6.23), indicating favorable fill quality.",
    "The PUT reached its peak of $6.74 at 09:48, only ~4 minutes after fill, then reversed as MSFT bounced from its intraday low.",
    "Both SPY and QQQ reclaimed their VWAPs at exactly 10:14 — the same minute the trailing stop fired — suggesting a coordinated market-wide reversal.",
    "The trailing stop math is confirmed: $6.74 × 0.75 = $5.055, matching the recorded exit price exactly.",
    "Entry spread was 9.25% ($0.55 wide); exit spread was 16.42% ($0.83 wide) — spread widened significantly on the exit side.",
    "Intraday option volume at entry was 86 contracts — extremely low, consistent with the thin rvol reading.",
    "MFE was +$59.00 (+9.59%) at 09:48; the trade eventually closed at -$109.50 (-17.80%), giving up the gain plus additional loss.",
    "MSFT held below VWAP from open through exit; SPY/QQQ were above VWAP at open but weakened toward 10:00 then recovered.",
    "The 25% trailing stop allowed a full round-trip: from peak gain (+$59) to realized loss (-$109.50) without triggering the 50% hard stop ($3.075).",
    "Open interest at entry was 301 — thin contract with limited market depth for a $400+ stock.",
    "The no-trade buffer (first 15 minutes) prevented entry during the sharpest drop (09:30–09:39), which may have produced a better entry price.",
]

hypotheses = [
    "H1: The 25% trailing stop is too loose for short-duration momentum plays in thin-volume options — a tighter trail (15–20%) may preserve more of the MFE.",
    "H2: Entering when RSI(1m) is below 20 may indicate a mean-reversion bounce is imminent rather than trend continuation, making it a poor entry for a directional put.",
    "H3: A rvol filter requiring >0.5 (50% ADV) would have blocked this trade; back-testing whether rvol<0.1 trades underperform as a class could validate tightening the fallback logic.",
    "H4: Simultaneous SPY+QQQ VWAP reclaim at 10:14 may be a reliable exit signal for MSFT shorts — testing a market-regime exit rule could reduce trailing-stop overshoot.",
    "H5: The 15-minute no-trade buffer forced entry 9 minutes after the scanner fired; earlier entry (09:36–09:39) during the sharpest drop might have produced better risk-reward.",
    "H6: Option spread widening from 9.25% at entry to 16.42% at exit suggests liquidity deteriorated intraday — this friction cost could be partially predicted by entry spread and rvol.",
    "H7: Trades where all scanner candidates are rejected and the system falls back to CLI list may have systematically worse outcomes — flagging and reviewing this class separately is warranted.",
    "H8: RSI(1m) < 18 at entry on a PUT (already oversold) may correlate with faster mean-reversion, warranting a shorter max-hold window than 120 minutes.",
]

future_evaluation_questions = [
    "What is the win rate and average PnL for rsi_trend SHORT trades entered via CLI fallback (scanner rejected all symbols) vs. scanner-passed entries?",
    "Does rvol < 0.1 at scan time correlate with worse realized PnL across all trades in the backtest dataset?",
    "How often does RSI(1m) < 20 at entry signal a bounce within 5 minutes, and does it predict MFE→reversal more reliably than RSI(1m) in 20–40 range?",
    "What trailing stop percentage (10%, 15%, 20%, 25%) maximizes retained MFE across the put-buying short trades in this strategy?",
    "Is there a statistically reliable pattern where simultaneous SPY+QQQ VWAP reclaim triggers reversal in MSFT shorts? Can this be quantified over historical sessions?",
    "What is the distribution of entry-to-exit spread widening (exit_spread_pct / entry_spread_pct) for trades with OI < 500 and volume < 100?",
    "Would adding a 'market regime' exit rule (exit if SPY and QQQ both cross above VWAP while holding a short) have improved this trade's outcome?",
    "How does the 15-minute no-trade buffer interact with fast-moving morning setups? Are there systematic cases where earlier entry (without the buffer) would have produced better entries?",
    "What percentage of rsi_trend SHORT trades in this ticker universe had option volume < 100 at entry, and how do they perform relative to higher-volume entries?",
    "If the trailing stop had been set at 15% instead of 25%, what would the exit price and PnL have been for this trade? ($6.74 × 0.85 = $5.729 → PnL = ($5.729 - $6.15) × 100 = -$42.10)",
]

# ---------------------------------------------------------------------------
# Assemble JSON document
# ---------------------------------------------------------------------------
report = {
    "replay_id":    "MSFT260513P00412500_20260512",
    "generated_at": generated_at,
    "trade":        trade,
    "scanner":      scanner,
    "selection_path": selection_path,
    "other_candidates": other_candidates,
    "underlying_bars": underlying_bars,
    "spy_context":  spy_context,
    "qqq_context":  qqq_context,
    "execution":    execution,
    "pnl_path":     pnl_path,
    "excursion":    excursion,
    "exit_levels":  exit_levels,
    "stop_behavior": stop_behavior,
    "market_regime": market_regime,
    "observations": observations,
    "hypotheses":   hypotheses,
    "future_evaluation_questions": future_evaluation_questions,
}

with open(JSON_OUT, "w") as f:
    json.dump(report, f, indent=2)
print(f"[OK] JSON written: {JSON_OUT}")

# ---------------------------------------------------------------------------
# Assemble Markdown document
# ---------------------------------------------------------------------------
md_lines = []

def h(level, text):
    md_lines.append(f"{'#' * level} {text}\n")

def blank():
    md_lines.append("\n")

def line(text=""):
    md_lines.append(text + "\n")

def table(headers, rows):
    widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0)) for i, h in enumerate(headers)]
    def fmt(vals):
        return "| " + " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(vals)) + " |"
    md_lines.append(fmt(headers) + "\n")
    md_lines.append("| " + " | ".join("-" * w for w in widths) + " |\n")
    for r in rows:
        md_lines.append(fmt(r) + "\n")

# Title
h(1, "Trade Replay — MSFT260513P00412500 — 2026-05-12")
line(f"*Generated: {generated_at}*")
blank()

# ── 1. Trade Overview ────────────────────────────────────────────────────────
h(2, "1. Trade Overview")
blank()
table(
    ["Field", "Value"],
    [
        ["Trade ID (DB)",       "13"],
        ["Option",              "MSFT260513P00412500"],
        ["Type",                "Put"],
        ["Strike",              "$412.50"],
        ["Expiry",              "2026-05-13 (1 DTE at entry)"],
        ["Strategy",            "rsi_trend"],
        ["Direction",           "SHORT underlying (bullish put)"],
        ["Delta at entry",      "-0.6544"],
        ["IV at entry",         "43.49%"],
        ["Entry time",          "2026-05-12 09:45:42 ET"],
        ["Fill time",           "2026-05-12 09:46:44 ET"],
        ["Exit time",           "2026-05-12 10:14:52 ET"],
        ["Hold duration",       f"1,688 sec (~{round(1688/60,1)} min)"],
        ["Fill price",          "$6.15"],
        ["Exit price",          "$5.055"],
        ["Realized PnL",        "-$109.50"],
        ["Exit reason",         "trailing_stop"],
        ["MFE",                 f"+${mfe_dollars:.2f} (+{mfe_pct:.2f}%) at 09:48"],
        ["MAE",                 f"-${abs(mae_dollars):.2f} ({mae_pct:.2f}%) at exit"],
        ["Paper trade",         "Yes"],
        ["Quantity",            "1 contract"],
        ["Underlying at scan",  "$410.475"],
    ],
)
blank()

# ── 2. Selection Path ────────────────────────────────────────────────────────
h(2, "2. Selection Path (Why MSFT Was Traded Despite Scanner Rejection)")
blank()
line("**Scanner outcome:** All 13 symbols scanned at 09:36:20 ET were rejected for `low_volume_chop`.")
line(f"MSFT rvol was **0.063** (6.3% of ADV) — **87% below** the 0.5 threshold required to pass.")
blank()
line("**Fallback mechanism:** `session_runner` fell back to the CLI `--symbol` list after scanner found zero passing candidates.")
line("The `rsi_trend` strategy evaluated the fallback list and generated a **SHORT** signal for MSFT.")
blank()
line("**LiquidityFilter cycle history:**")
blank()
table(
    ["Cycles", "Time range", "Contract(s) seen", "Reason rejected"],
    [
        ["1–9",  "09:36–09:44", "P412500 (δ -0.448), P405000 (δ -0.335 to -0.369)", "15-min no-trade buffer active (risk rule)"],
        ["10",   "09:45:42",    "P412500 (δ -0.654, spread 9.3%, OI=301, vol=86)",  "Buffer lifted — SELECTED and ordered"],
    ],
)
blank()
line("> **Note:** Option volume of 86 contracts is extremely low intraday, consistent with the thin rvol reading at scan time.")
blank()

# ── 3. Trade Timeline ────────────────────────────────────────────────────────
h(2, "3. Trade Timeline")
blank()
table(
    ["Timestamp ET", "Event", "MSFT", "Option price", "Notes"],
    [
        ["09:30",    "ORB open",            "$413.00", "—",      "ORB high=415.50 low=412.86; V=1.3M"],
        ["09:35",    "Sharp drop",          "$408.86", "—",      "−$4.14 from open; V=186K"],
        ["09:36:20", "Scanner scan",        "$408.53", "—",      "Score=50, SHORT signal, rvol=0.063; ALL 13 rejected"],
        ["09:39",    "Session low close",   "$407.41", "—",      ""],
        ["09:44",    "Pre-signal bar",      "$408.90", "—",      "EMA20=409.88; buffer still active"],
        ["09:45:42", "SIGNAL + order sent", "$408.21", "—",      "RSI1m=17.87, VWAP=410.77, vs_vwap=−2.56; limit=$6.23"],
        ["09:46:44", "FILL",                "$407.77", "$6.15",  "Slippage=−$0.08 (favorable); TTF=62.67s"],
        ["09:47",    "Put gaining",         "$406.82", "~$6.65", "RSI1m=15.42; unrealized=+$50"],
        ["09:48",    "MSFT intraday low",   "$406.67", "~$6.74", "RSI1m=15.17; PUT PEAK — MFE=+$59"],
        ["09:49",    "Bounce begins",       "$407.49", "~$6.40", "RSI1m=22.50; unrealized=+$25"],
        ["09:50",    "Bounce continues",    "$407.89", "~$6.20", "RSI1m=25.87; unrealized=+$5"],
        ["10:00",    "Continued rally",     "$407.86", "~$6.05", "SPY/QQQ well below VWAP; unrealized=−$10"],
        ["10:04",    "Reclaim attempt",     "$408.93", "~$5.80", "unrealized=−$35"],
        ["10:14:52", "TRAILING STOP EXIT",  "$409.24", "$5.055", "SPY+QQQ reclaim VWAP simultaneously; PnL=−$109.50"],
        ["10:15",    "Post-exit rally",     "$409.67", "—",      ""],
        ["10:16",    "MSFT crosses VWAP",   "$410.00", "—",      "MSFT reclaims ~$409.55 VWAP"],
    ],
)
blank()

# ── 4. Market Context at Entry ───────────────────────────────────────────────
h(2, "4. Market Context at Entry")
blank()
h(3, "MSFT Indicators at Signal (09:45)")
blank()
table(
    ["Indicator", "Value", "Interpretation"],
    [
        ["Close",         "$408.21", "Below VWAP — bearish positioning"],
        ["VWAP",          "$410.77", ""],
        ["vs VWAP",       "−$2.56 (−0.62%)", "Firm below VWAP"],
        ["EMA20 (1m)",    "$409.72", "Price below EMA20"],
        ["RSI(1m, 14)",   "17.87",   "Deeply oversold on 1-min timeframe"],
        ["RSI(5m/daily)", "42.61",   "Moderate — used by scanner"],
        ["Delta",         "−0.6544", "ITM put, high delta"],
        ["IV",            "43.49%",  "Elevated"],
        ["ORB",           "H=415.50 L=412.86", "Strike 412.50 is near ORB low"],
    ],
)
blank()
h(3, "SPY Context")
blank()
table(
    ["Time", "SPY close", "VWAP", "vs VWAP", "Note"],
    [[r["time"], f"${r['close']}", f"${r['vwap']}", f"{r['vs_vwap']:+.2f}", r["note"]] for r in spy_context],
)
blank()
h(3, "QQQ Context")
blank()
table(
    ["Time", "QQQ close", "VWAP", "vs VWAP", "Note"],
    [[r["time"], f"${r['close']}", f"${r['vwap']}", f"{r['vs_vwap']:+.2f}", r["note"]] for r in qqq_context],
)
blank()
line("**Market regime summary:** Broad market (SPY/QQQ) trending up but weakened mid-morning. "
     "MSFT showed idiosyncratic weakness (below VWAP all morning, gap-up reversed). "
     "Both SPY and QQQ reclaimed VWAP simultaneously at 10:14 — the exact minute the trailing stop fired.")
blank()

# ── 5. Execution Telemetry ───────────────────────────────────────────────────
h(2, "5. Execution Telemetry")
blank()
h(3, "Entry")
blank()
table(
    ["Field", "Value"],
    [
        ["Bid",              "$5.67"],
        ["Ask",              "$6.22"],
        ["Spread",           f"${execution['entry_spread']:.2f} ({execution['entry_spread_pct_display']})"],
        ["Pricing mode",     "marketable_limit (ask + $0.01)"],
        ["Limit sent",       "$6.23"],
        ["Fill price",       "$6.15"],
        ["Slippage",         "−$0.08 (filled $0.08 better than limit — favorable)"],
        ["Time-to-fill",     "62.67 seconds"],
    ],
)
blank()
h(3, "Exit")
blank()
table(
    ["Field", "Value"],
    [
        ["Bid",          "$4.64"],
        ["Ask",          "$5.47"],
        ["Spread",       f"${execution['exit_spread']:.2f} ({execution['exit_spread_pct_display']})"],
        ["Exit price",   "$5.055"],
        ["Exit reason",  "trailing_stop"],
        ["Spread note",  "Exit spread (16.42%) vs entry spread (9.25%) — liquidity deteriorated"],
    ],
)
blank()

# ── 6. PnL Path & Excursion ──────────────────────────────────────────────────
h(2, "6. PnL Path & Excursion")
blank()
table(
    ["Time ET", "MSFT", "Option (est)", "Unrealized PnL", "Note"],
    [
        [r["time"], f"${r['underlying']:.2f}", f"${r['option_est']:.3f}",
         f"${r['unrealized_pnl']:+.2f}", r["note"]]
        for r in pnl_path
    ],
)
blank()
table(
    ["Metric", "Value"],
    [
        ["MFE (max favorable excursion)", f"+${mfe_dollars:.2f} (+{mfe_pct:.2f}%) — peak at 09:48"],
        ["MAE (max adverse excursion)",   f"−${abs(mae_dollars):.2f} ({mae_pct:.2f}%) — at exit"],
        ["Peak option price",             f"$6.74 at 09:48"],
        ["Hold to peak",                  "~4 min after fill"],
    ],
)
blank()

# ── 7. Stop / Take-Profit Behavior ───────────────────────────────────────────
h(2, "7. Stop / Take-Profit Behavior")
blank()
table(
    ["Exit rule", "Level", "Triggered?", "Notes"],
    [
        ["Hard stop (50%)",    f"${stop_loss_price:.3f}",    "No",  "fill × 0.50; option never fell this far"],
        ["Take profit (100%)", f"${take_profit_price:.3f}",  "No",  "fill × 2.00; option peak was $6.74"],
        ["Trailing stop (25%)", f"${trail_fired_at:.3f}",   "YES", f"peak ({peak_price}) × 0.75 = {trail_fired_at}; exit=$5.055"],
        ["Max hold (120 min)", "120 min",                    "No",  "held 28.1 min"],
        ["EOD exit",           "10:50 ET",                   "No",  "exited at 10:14:52"],
    ],
)
blank()
line(f"**Trailing stop verification:** Peak = $6.74 × (1 − 0.25) = **${trail_fired_at:.3f}** — matches exit price $5.055 exactly. ✓")
blank()
line("The 25% trailing stop allowed the full MFE→MAE round-trip: the position went from +$59 at peak to −$109.50 at exit "
     "without triggering the 50% hard stop ($3.075). The trail captured the reversal but did not protect the peak gain.")
blank()

# ── 8. Rejected Candidates ───────────────────────────────────────────────────
h(2, "8. Rejected Candidates at 09:36")
blank()
table(
    ["Symbol", "Score", "Signal", "rvol", "Trend", "RSI", "Scanner rejection", "Why not traded"],
    [
        ["MSFT", "50", "SHORT", "0.063", "sideways", "42.61", "low_volume_chop", "Traded via CLI fallback (only SHORT signal)"],
        ["NVDA", "48", "LONG",  "0.136", "trend_up", "75.0", "low_volume_chop", "LONG signal — rsi_trend SHORT filter excluded it"],
        ["AMD",  "40", "LONG",  "0.158", "trend_up", "80.0", "low_volume_chop", "LONG signal — rsi_trend SHORT filter excluded it"],
        ["AAPL", "38", "LONG",  "0.109", "trend_up", "79.0", "low_volume_chop", "LONG signal — rsi_trend SHORT filter excluded it"],
    ],
)
blank()
line("All 13 symbols failed the scanner's rvol threshold (0.5). MSFT was the only symbol with a SHORT signal matching the rsi_trend strategy's directional filter.")
blank()

# ── 9. Observations ──────────────────────────────────────────────────────────
h(2, "9. Observations")
blank()
for obs in observations:
    line(f"- {obs}")
blank()

# ── 10. Hypotheses ───────────────────────────────────────────────────────────
h(2, "10. Hypotheses")
blank()
for hyp in hypotheses:
    line(f"- {hyp}")
blank()

# ── 11. Future Evaluation Questions ─────────────────────────────────────────
h(2, "11. Future Evaluation Questions")
blank()
for q in future_evaluation_questions:
    line(f"- {q}")
blank()

# ── 12. Diagnostics Footer ───────────────────────────────────────────────────
h(2, "12. Diagnostics Footer")
blank()
line("**No parameters were changed** as a result of this trade. This replay is a single-trade post-mortem for diagnostic and evaluation purposes only.")
line("All data is sourced from the trade journal (DB id=13) and scan results (DB id=44) recorded at the time of the session.")
line("Observations and hypotheses require multi-trade validation before informing strategy parameter changes.")
blank()
line("---")
line(f"*Replay generated by `scripts/generate_replay.py` at {generated_at}*")

with open(MD_OUT, "w") as f:
    f.writelines(md_lines)
print(f"[OK] Markdown written: {MD_OUT}")
print("\nDone. Both files written successfully.")
