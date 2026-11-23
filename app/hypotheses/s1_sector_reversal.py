"""Sector beta is fitted only on completed prior sessions."""
from collections import defaultdict
from datetime import time
from statistics import median

import numpy as np

from app.research.equity_observer import ET, INTERVAL
from app.research.replay.engine import HypothesisSignal
from .sector import session_start


def closes_by_symbol(session):
    closes = defaultdict(dict)
    for bar in session.current():
        closes[bar.symbol][bar.start] = bar.close
    return closes


def fit_beta(prior, symbol, sector):
    paired = []
    session_count = 0
    for closes in prior:
        stock, peer = closes.get(symbol, {}), closes.get(sector, {})
        times = sorted(set(stock) & set(peer))
        samples = [(stock[t] / stock[t - INTERVAL] - 1, peer[t] / peer[t - INTERVAL] - 1)
                   for t in times if t - INTERVAL in stock and t - INTERVAL in peer]
        if samples:
            session_count += 1
            paired.extend(samples)
    if len(paired) < 100 or session_count < 15:
        return None
    y, x = np.asarray(paired).T
    if np.std(x) == 0:
        return None
    design = np.column_stack((np.ones(len(x)), x))
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    sigma = float(np.std(y - design @ coefficients, ddof=1))
    return (float(coefficients[1]), sigma) if sigma > 0 else None


class SectorReversal:
    id = "S1"
    paired = False
    control = "ordinary_dip"

    def __init__(self, mapping):
        self.mapping = mapping
        self.symbols = tuple(dict.fromkeys((*mapping, *mapping.values())))
        self._trigger_drawdowns = defaultdict(list)
        self._closes = {}

    def prepare(self, prior):
        # Cache each prior session once and discard dates outside the rolling window.
        current = {}
        for session in prior[-20:]:
            current[session.day] = self._closes.get(session.day)
            if current[session.day] is None:
                current[session.day] = closes_by_symbol(session)
        self._closes = current
        fits = {symbol: fit_beta(current.values(), symbol, sector)
                for symbol, sector in self.mapping.items()}
        thresholds = {symbol: median(values) for symbol, values in self._trigger_drawdowns.items()}
        return ReversalDay(self.mapping, fits, thresholds)

    def finish(self, signals):
        for signal in signals:
            if signal.hypothesis_id == self.id:
                self._trigger_drawdowns[signal.symbol].append(signal.features["drawdown"])


class ReversalDay:
    def __init__(self, mapping, fits, thresholds):
        self.mapping = mapping
        self.fits = fits
        self.thresholds = thresholds
        self.processed = set()

    def __call__(self, view):
        if view.at.astimezone(ET).time() < time(10):
            return
        changed = {b.symbol for b in view.changed}
        start = session_start(view.day)
        for symbol, sector in self.mapping.items():
            fit = self.fits[symbol]
            if fit is None or not ({symbol, sector} & changed):
                continue
            stock, peer = view.bars(symbol), view.bars(sector)
            if len(stock) < 2 or not peer or stock[0].start != start or peer[0].start != start:
                continue
            latest = stock[-1]
            if latest.start != peer[-1].start or (symbol, latest.start) in self.processed:
                continue
            count = int((latest.end - start) / INTERVAL)
            if len(stock) != count or len(peer) != count:
                continue
            self.processed.add((symbol, latest.start))
            if latest.low < min(b.low for b in stock[:-1]) or latest.close < latest.open:
                continue
            beta, sigma_bar = fit
            raw = latest.close / stock[0].open - 1
            sector_return = peer[-1].close / peer[0].open - 1
            # Drawdown at decision is measured from the open to the latest close.
            residual = raw - beta * sector_return
            scale = sigma_bar * count ** 0.5
            features = {"beta": beta, "residual": residual, "scaled_sigma": scale,
                        "sector_return": sector_return, "drawdown": -raw, "sector": sector}
            if residual <= -1.5 * scale and sector_return >= -0.0025:
                yield HypothesisSignal("S1", symbol, view.at, features=features)
            if sector_return < -0.0025 and -raw > 0 and -raw >= self.thresholds.get(symbol, float("inf")):
                yield HypothesisSignal("ordinary_dip", symbol, view.at,
                                      features=features, control_of="S1")
