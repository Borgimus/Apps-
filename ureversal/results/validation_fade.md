# U-Reversal Validation Report

**Verdict: FAIL**

- Feed: `sip` · Sessions: 253 (2025-07-01 → 2026-07-02)

## Checks

- ❌ sufficient_events
- ❌ dia_leads_xcorr
- ❌ dia_leads_timing
- ❌ edge_positive_after_costs
- ✅ beats_null_random
- ✅ beats_null_shuffled

## §8.1 Frequency

- Events: 28 over 253 sessions (0.11/day; 10% of days had ≥1)

## §8.2 Does DIA lead SPY?

- Cross-correlation lead score: -0.011 (95% CI [-0.115, 0.097]) → no significant lead
- Reversal timing (SPY cross − DIA cross): median 1.0s, Wilcoxon p=0.1708 → not significant

## §8.3 Net edge vs null models (bps, after costs)

| Horizon | n | mean | 95% CI | win% | null-random p95 | null-shuffled p95 | beats both |
|---|---|---|---|---|---|---|---|
| 30s | 28 | -0.03 | [-2.09, 2.05] | 50% | -0.25 | rate collapsed (0 ev) | ✅ |
| 60s | 28 | 0.44 | [-2.31, 3.17] | 54% | 0.37 | rate collapsed (0 ev) | ✅ |
| 120s | 28 | 0.82 | [-2.88, 4.61] | 50% | 0.90 | rate collapsed (0 ev) | ❌ |
| 300s | 28 | 2.18 | [-3.43, 7.67] | 50% | 1.23 | rate collapsed (0 ev) | ✅ |

## §8.5 Regime robustness (net bps @ reference horizon)

| Regime | days | events | mean net bps |
|---|---|---|---|
| low vol | 85 | 0 | nan |
| mid vol | 84 | 5 | 0.90 |
| high vol | 84 | 23 | 0.34 |
