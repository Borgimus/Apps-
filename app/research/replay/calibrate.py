"""Independent checks on the replay timebase and clustered inference."""
from collections import defaultdict
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.research.equity_observer import ET, Observer
from .sources import index_current
from .stats import newey_west


def frozen_parity(root: Path, session):
    with TemporaryDirectory() as directory:
        observer = Observer(root, Path(directory), list(session.symbols),
                            session.day.isoformat(), code_sha=session.code_sha)
        for at, batch in session.arrivals():
            incoming = defaultdict(list)
            for bar in batch:
                incoming[bar.symbol].append(bar.frozen_row())
            observer.cycle(incoming, at.astimezone(ET))
        fields = ("id", "confirmed_at", "observed_at", "entry_bar")
        def keys(records):
            return sorted(tuple(r[k] for k in fields) for r in records)
        expected = keys(session.recorded_signals)
        actual = keys(observer.state["signals"].values())
    if not expected or actual != expected:
        raise ValueError(f"Frozen signal parity failed: expected {len(expected)}, produced {len(actual)}")
    return len(expected)


def random_entry_rejection(sessions, *, seeds=200):
    """Null compares two time-equivalent random-entry arms within each real day."""
    days = []
    for session in sessions:
        bars = index_current(session).get("SPY", {})
        ordered = sorted(bars)
        moves = []
        for i, start in enumerate(ordered[:-5]):
            end = ordered[i + 5]
            if start.astimezone(ET).time() <= time(11, 30) and (end - start).total_seconds() == 25 * 60:
                moves.append((bars[end].close / bars[start].open - 1) * 1e4)
        if len(moves) >= 2:
            days.append(moves)
    if len(days) < 10:
        raise ValueError("Calibration requires at least 10 real SPY sessions")
    passed_nulls = 0
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        differences = []
        for moves in days:
            left, right = rng.choice(len(moves), size=2, replace=False)
            differences.append(moves[left] - moves[right])
        statistic = newey_west(differences, lag=6)["t"]
        passed_nulls += statistic is not None and statistic >= 3.0
    return {"seeds": seeds, "days": len(days), "rejected": seeds - passed_nulls,
            "rejection_rate": (seeds - passed_nulls) / seeds}
