# Strategy Readiness Snapshot

**Generated:** 2026-07-14 09:33:19 ET  
**Session date:** 2026-07-14  
**Bar interval:** 5m  

## Active Configuration

| Strategy | `strategy_id` | `min_bars_required` | Earliest Ready (ET) |
|---|---|---|---|
| Opening Range Breakout | `orb` | 17 | 10:55 |
| VWAP Reclaim/Rejection | `vwap_reclaim` | 7 | 10:05 |
| RSI + Trend Filter | `rsi_trend` | 55 | 14:05 |

### RSI_trend Active Parameters

| Parameter | Value |
|---|---|
| `rsi_period` | 14 |
| `rsi_oversold` | 35.0 |
| `rsi_overbought` | 65.0 |
| `trend_ema_period` | 50 |
| `bar_interval` | `5m` |
| `mode` | `standard` |

## Per-Symbol Readiness

### Opening Range Breakout (`orb`)

- `min_bars_required`: **17**
- Earliest ready: **10:55 ET**
- Currently ready: **1/1 symbols**

| Symbol | Group | Bars Today | Total Bars | Ready | Reason |
|---|---|---|---|---|---|
| SPY | cli_override | 1 | 168 | ✓ | sufficient bars |

### VWAP Reclaim/Rejection (`vwap_reclaim`)

- `min_bars_required`: **7**
- Earliest ready: **10:05 ET**
- Currently ready: **1/1 symbols**

| Symbol | Group | Bars Today | Total Bars | Ready | Reason |
|---|---|---|---|---|---|
| SPY | cli_override | 1 | 168 | ✓ | sufficient bars |

### RSI + Trend Filter (`rsi_trend`)

- `min_bars_required`: **55**
- Earliest ready: **14:05 ET**
- Currently ready: **1/1 symbols**

| Symbol | Group | Bars Today | Total Bars | Ready | Reason |
|---|---|---|---|---|---|
| SPY | cli_override | 1 | 168 | ✓ | sufficient bars |

## Summary

- **Opening Range Breakout**: 1/1 symbols ready  (earliest: 10:55 ET)
- **VWAP Reclaim/Rejection**: 1/1 symbols ready  (earliest: 10:05 ET)
- **RSI + Trend Filter**: 1/1 symbols ready  (earliest: 14:05 ET)

---

*No orders placed. No config changed. Diagnostic read-only.*
