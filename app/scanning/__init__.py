"""
Scanning pipeline — research-grade candidate selection.

Pipeline stages:
  1. UniverseLoader   — load/filter the ticker universe from YAML
  2. YFinanceScanner  — compute intraday metrics (ATR, RVOL, VWAP, RSI, …)
  3. CandidateScorer  — rank symbols 0-100 and emit signal type + reason codes
  4. AlpacaConfirmer  — verify option chain liquidity via Alpaca before trading

yfinance is for research and screening only.
All execution quotes, chains, and orders go through Alpaca.
"""

from .alpaca_confirmer import AlpacaConfirmer, ConfirmedCandidate
from .candidate_scorer import CandidateScore, CandidateScorer
from .universe_loader import UniverseLoader
from .yfinance_scanner import SymbolMetrics, YFinanceScanner

__all__ = [
    "UniverseLoader",
    "YFinanceScanner",
    "SymbolMetrics",
    "CandidateScorer",
    "CandidateScore",
    "AlpacaConfirmer",
    "ConfirmedCandidate",
]
