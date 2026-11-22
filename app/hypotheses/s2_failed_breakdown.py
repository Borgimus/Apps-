"""First three-bar failed breakdown, on complete five-minute ranges."""
from datetime import timedelta

from app.research.equity_observer import INTERVAL
from app.research.replay.engine import HypothesisSignal
from .sector import session_start

PRIMARY = ("SPY", "QQQ")
DIAGNOSTIC = ("IWM", "DIA")


class FailedBreakdown:
    id = "S2"
    paired = False
    control = "inverse_orb"
    symbols = PRIMARY + DIAGNOSTIC

    def prepare(self, prior):
        return self

    def __call__(self, view):
        start = session_start(view.day)
        if view.at < start + timedelta(minutes=25):
            return
        for symbol in sorted({b.symbol for b in view.changed} & set(self.symbols)):
            bars = view.bars(symbol)
            by_t = {b.start: b for b in bars}
            opening = [by_t.get(start + INTERVAL * i) for i in range(3)]
            if any(b is None for b in opening):
                continue
            latest = bars[-1]
            low = min(b.low for b in opening)
            if latest.close <= low:
                continue
            previous = []
            for lag in range(1, 4):
                t = latest.start - lag * INTERVAL
                if t < start + INTERVAL * 3 or t not in by_t:
                    break
                previous.append(by_t[t])
            if not previous:
                continue
            broken = next((b for b in previous if b.close < low), None)
            if broken is not None and all(b.close <= low for b in previous if b.start > broken.start):
                name = self.id if symbol in PRIMARY else "S2_secondary_universe"
                yield HypothesisSignal(name, symbol, view.at, features={"opening_low": low,
                    "breakdown_at": broken.start.isoformat(), "reclaim_at": latest.start.isoformat()})
