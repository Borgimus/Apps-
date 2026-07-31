"""Walk-forward splitting, window generation, and parameter-sensitivity reporting."""
import pytest

from src.backtest.walk_forward import (
    sensitivity,
    three_way_split,
    walk_forward_windows,
)


def test_three_way_split_no_overlap():
    s = three_way_split(100, dev=0.5, val=0.25)
    assert s.development == (0, 50)
    assert s.validation == (50, 75)
    assert s.final_oos == (75, 100)
    # contiguous, non-overlapping, covers the whole range
    assert s.development[1] == s.validation[0] and s.validation[1] == s.final_oos[0]


def test_split_rejects_bad_fractions():
    with pytest.raises(ValueError):
        three_way_split(100, dev=0.8, val=0.5)


def test_walk_forward_windows_are_ordered_and_disjoint():
    wins = list(walk_forward_windows(10, train=4, test=2, step=2))
    assert wins[0] == ((0, 4), (4, 6))
    assert wins[1] == ((2, 6), (6, 8))
    for tr, te in wins:
        assert tr[1] == te[0] and tr[0] < tr[1] < te[1]   # train strictly before test


def test_sensitivity_reports_distribution_without_picking_winner():
    grid = [{"slope": 0.0}, {"slope": 0.1}, {"slope": 0.2}]
    # A fake metric that is stable across the region.
    out = sensitivity(lambda p: 1.0 + p["slope"] * 0.0, grid)
    assert out["n"] == 3 and out["mean"] == 1.0 and out["stdev"] == 0.0
    assert "points" in out and len(out["points"]) == 3
