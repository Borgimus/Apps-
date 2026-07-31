"""Strength ranking (no future data) and agreement modes."""
from src.scanner.strength import agreement_sets, membership, rank_top_percentile


def test_rank_top_percentile_selects_best():
    closes = {
        "A": [100, 100, 100, 200],   # +100%
        "B": [100, 100, 100, 150],   # +50%
        "C": [100, 100, 100, 110],   # +10%
        "D": [100, 100, 100, 105],   # +5%
    }
    top = rank_top_percentile(closes, lookback_bars=3, top_percentile=25)
    assert top == ["A"]  # ceil(4*0.25)=1


def test_rank_excludes_insufficient_history():
    closes = {"A": [100, 200], "B": [100, 100, 100, 150]}
    top = rank_top_percentile(closes, lookback_bars=3, top_percentile=100)
    assert top == ["B"]  # A lacks enough bars, excluded (no future data invented)


def test_rank_tiebreak_deterministic():
    closes = {"B": [100, 120], "A": [100, 120]}
    top = rank_top_percentile(closes, lookback_bars=1, top_percentile=100)
    assert top == ["A", "B"]  # equal return -> symbol ascending


def test_agreement_sets():
    sets = agreement_sets(
        one_month=["AAA", "BBB", "CCC"],
        three_month=["BBB", "CCC", "DDD"],
        six_month=["CCC", "DDD", "EEE"],
    )
    assert sets["intersection_3_of_3"] == ["CCC"]
    assert sets["agreement_2_of_3"] == ["BBB", "CCC", "DDD"]
    assert sets["union_ranked"] == ["AAA", "BBB", "CCC", "DDD", "EEE"]


def test_membership_counts():
    scans = {"one_month": ["AAA"], "three_month": ["AAA", "BBB"], "six_month": ["BBB"]}
    m = membership("AAA", scans)
    assert m["agreement_count"] == 2
    assert m["one_month"] is True and m["six_month"] is False
