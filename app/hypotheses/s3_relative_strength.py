"""Compare matched sector exposure at exactly the same decision time."""
from datetime import timedelta

from app.research.replay.controls import substitute
from app.research.replay.engine import HypothesisSignal
from .sector import pair_at, session_start


class RelativeStrength:
    id = "S3"
    paired = True
    control = "sector_etf"

    def __init__(self, mapping):
        self.mapping = mapping
        self.symbols = tuple(dict.fromkeys((*mapping, *mapping.values())))

    def prepare(self, prior):
        return self

    def __call__(self, view):
        first = session_start(view.day)
        last = first + timedelta(minutes=25)
        if not any(bar.start in (first, last) for bar in view.changed):
            return
        for stock, sector in self.mapping.items():
            opens = pair_at(view, stock, sector, first)
            ends = pair_at(view, stock, sector, last)
            if opens is None or ends is None:
                continue
            if view.at != max(b.available_at for b in opens + ends):
                continue
            sector_return = ends[1].close / opens[1].open - 1
            excess = (ends[0].close / opens[0].open - 1) - sector_return
            if excess < 0.005:
                continue
            features = {"sector": sector, "sector_return": sector_return, "excess": excess}
            signal = HypothesisSignal(self.id, stock, view.at, features=features)
            if sector_return > 0:
                yield signal
                # Identity preserves the stock for several candidates matched to one ETF.
                yield substitute(signal, self.control + "." + stock, symbol=sector)
            elif sector_return < 0:
                yield HypothesisSignal("sector_down", stock, view.at, features=features, control_of="S3")
