"""Controls use the same clock and exit model as the primary signals."""
from dataclasses import replace
from datetime import datetime

from app.research.equity_observer import ET


def substitute(signal, control_id, *, symbol=None):
    return replace(signal, hypothesis_id=control_id, symbol=symbol or signal.symbol,
                   direction=1, control_of=signal.hypothesis_id)


def retime(signal, day, control_id):
    local_at = signal.decided_at.astimezone(ET)
    return replace(substitute(signal, control_id),
                   decided_at=datetime.combine(day, local_at.time(), ET))
