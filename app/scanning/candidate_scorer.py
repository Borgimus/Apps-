"""
Candidate Scorer — ranks symbols 0-100 based on intraday metrics.

Score factors (additive):
  - Relative volume         0-25 pts
  - ATR (movement potential) 0-15 pts
  - Opening range breakout  0-20 pts  (directional signal)
  - Trend alignment         0-15 pts
  - RSI health              0-10 pts  (not overbought/oversold chop)
  - VWAP alignment          0-10 pts
  - MA compression          0-5  pts  (coiling setup)

Rejection criteria (any one fails → is_rejected=True):
  - has_earnings_today
  - rvol < 0.5 (low-volume chop)
  - atr_pct < 0.002 (too narrow to trade options on)
  - score < min_scan_score (configurable threshold, default 40)
  - errors present in metrics (data fetch failed)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .yfinance_scanner import SymbolMetrics

logger = logging.getLogger(__name__)

# Score weights
_RVOL_HIGH    = 25   # rvol >= 2.0
_RVOL_MED     = 15   # rvol >= 1.5
_RVOL_LOW     = 5    # rvol >= 1.0
_ATR_HIGH     = 15   # atr_pct >= 0.015
_ATR_MED      = 8    # atr_pct >= 0.008
_ATR_LOW      = 3    # atr_pct >= 0.004
_ORB_BREAK    = 20   # clean ORB breakout
_ORB_PARTIAL  = 8    # near ORB (within 0.2%)
_TREND_ALIGN  = 15   # trend aligned with signal direction
_TREND_PART   = 7    # sideways trend
_RSI_HEALTH   = 10   # RSI 35-65
_RSI_PART     = 5    # RSI 30-70
_VWAP_ALIGN   = 10   # price above VWAP for LONG, below for SHORT
_MA_COMPRESS  = 5    # MA compression detected


@dataclass
class CandidateScore:
    symbol: str
    score: float                    # 0-100
    signal_type: str                # "LONG" | "SHORT" | "NEUTRAL"
    reason_codes: List[str]         # why score is high
    rejected_reasons: List[str]     # why rejected (empty if passing)
    is_rejected: bool
    metrics: SymbolMetrics
    universe_group: Optional[str] = None


class CandidateScorer:
    """
    Scores SymbolMetrics and determines signal direction.

    Usage:
        scorer = CandidateScorer(min_scan_score=40.0)
        scores = scorer.score_all(metrics_list)
    """

    def __init__(
        self,
        min_scan_score: float = 40.0,
        min_underlying_price: float = 0.0,
        min_underlying_avg_volume: int = 0,
    ):
        self._min_score = min_scan_score
        self._min_underlying_price = min_underlying_price
        self._min_underlying_avg_volume = min_underlying_avg_volume

    def score_all(self, metrics_list: List[SymbolMetrics]) -> List[CandidateScore]:
        candidates = [self._score_one(m) for m in metrics_list]
        # Sort by score descending (rejected symbols go last)
        return sorted(candidates, key=lambda c: (not c.is_rejected, c.score), reverse=True)

    def score_one(self, metrics: SymbolMetrics) -> CandidateScore:
        return self._score_one(metrics)

    def _score_one(self, m: SymbolMetrics) -> CandidateScore:
        score = 0.0
        reasons: List[str] = []
        rejections: List[str] = []

        # ── Hard rejections ────────────────────────────────────────────────────
        if m.errors:
            rejections.append("data_fetch_error")
        if m.has_earnings_today:
            rejections.append("earnings_today")
        if m.rvol < 0.5:
            rejections.append("low_volume_chop")
        if m.atr_pct < 0.002:
            rejections.append("atr_too_small")
        if m.price <= 0:
            rejections.append("invalid_price")
        if self._min_underlying_price > 0 and 0 < m.price < self._min_underlying_price:
            rejections.append("price_too_low")
        if self._min_underlying_avg_volume > 0 and m.avg_volume_20d < self._min_underlying_avg_volume:
            rejections.append("insufficient_underlying_volume")

        # ── Signal direction ───────────────────────────────────────────────────
        signal_type = self._determine_signal(m)

        # ── Scoring (applied even for rejected symbols, for ranking info) ─────
        # Relative volume
        if m.rvol >= 2.0:
            score += _RVOL_HIGH
            reasons.append(f"rvol={m.rvol:.2f}x (high)")
        elif m.rvol >= 1.5:
            score += _RVOL_MED
            reasons.append(f"rvol={m.rvol:.2f}x (elevated)")
        elif m.rvol >= 1.0:
            score += _RVOL_LOW
            reasons.append(f"rvol={m.rvol:.2f}x (normal)")

        # ATR movement potential
        if m.atr_pct >= 0.015:
            score += _ATR_HIGH
            reasons.append(f"atr={m.atr_pct:.2%} (wide-range)")
        elif m.atr_pct >= 0.008:
            score += _ATR_MED
            reasons.append(f"atr={m.atr_pct:.2%} (good range)")
        elif m.atr_pct >= 0.004:
            score += _ATR_LOW
            reasons.append(f"atr={m.atr_pct:.2%} (minimal range)")

        # Opening range breakout / breakdown
        if signal_type == "LONG" and m.is_orb_breakout:
            score += _ORB_BREAK
            reasons.append("orb_breakout")
        elif signal_type == "SHORT" and m.is_orb_breakdown:
            score += _ORB_BREAK
            reasons.append("orb_breakdown")
        elif m.opening_range_high > 0 and m.price > 0:
            # Near ORB: within 0.2% of the range
            near_break = abs(m.price - m.opening_range_high) / m.opening_range_high < 0.002
            near_down  = abs(m.price - m.opening_range_low)  / m.opening_range_low  < 0.002
            if near_break or near_down:
                score += _ORB_PARTIAL
                reasons.append("near_orb_level")

        # Trend alignment
        if signal_type == "LONG" and m.trend == "up":
            score += _TREND_ALIGN
            reasons.append("trend_up")
        elif signal_type == "SHORT" and m.trend == "down":
            score += _TREND_ALIGN
            reasons.append("trend_down")
        elif m.trend == "sideways":
            score += _TREND_PART
            reasons.append("trend_sideways")

        # RSI health (not overbought/oversold chop)
        if 35 <= m.rsi <= 65:
            score += _RSI_HEALTH
            reasons.append(f"rsi={m.rsi:.0f} (healthy)")
        elif 30 <= m.rsi <= 70:
            score += _RSI_PART
            reasons.append(f"rsi={m.rsi:.0f} (ok)")
        else:
            reasons.append(f"rsi={m.rsi:.0f} (extreme)")

        # VWAP alignment
        if signal_type == "LONG" and m.price_vs_vwap == "above":
            score += _VWAP_ALIGN
            reasons.append("above_vwap")
        elif signal_type == "SHORT" and m.price_vs_vwap == "below":
            score += _VWAP_ALIGN
            reasons.append("below_vwap")

        # MA compression — coiling setup
        if m.ma_compression:
            score += _MA_COMPRESS
            reasons.append("ma_compression")

        # ── Score-based rejection ─────────────────────────────────────────────
        score = min(round(score, 2), 100.0)
        if score < self._min_score and not rejections:
            rejections.append(f"score_below_threshold ({score:.1f} < {self._min_score})")

        is_rejected = len(rejections) > 0

        logger.debug(
            "CandidateScorer: %s score=%.1f signal=%s rejected=%s reasons=%s",
            m.symbol, score, signal_type, is_rejected, reasons,
        )

        return CandidateScore(
            symbol=m.symbol,
            score=score,
            signal_type=signal_type,
            reason_codes=reasons,
            rejected_reasons=rejections,
            is_rejected=is_rejected,
            metrics=m,
            universe_group=m.universe_group,
        )

    @staticmethod
    def _determine_signal(m: SymbolMetrics) -> str:
        """
        Determine direction based on VWAP and trend signals.
        Returns "LONG", "SHORT", or "NEUTRAL".
        """
        bullish = 0
        bearish = 0

        if m.price_vs_vwap == "above":
            bullish += 1
        elif m.price_vs_vwap == "below":
            bearish += 1

        if m.trend == "up":
            bullish += 1
        elif m.trend == "down":
            bearish += 1

        if m.is_orb_breakout:
            bullish += 1
        elif m.is_orb_breakdown:
            bearish += 1

        if bullish > bearish:
            return "LONG"
        if bearish > bullish:
            return "SHORT"
        return "NEUTRAL"
