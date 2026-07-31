"""TC2000 importer: atomic batch, validation, stale/incomplete rejection, candidate sets."""
from datetime import date

import pytest

from src.tc2000.importer import ImportError_, build_batch, parse_symbols


def files(d="2026-07-31", one="AAA\nBBB\nCCC", three="BBB\nCCC\nDDD", six="CCC\nDDD\nEEE"):
    return {
        f"strength_1m_{d}.txt": one,
        f"strength_3m_{d}.txt": three,
        f"strength_6m_{d}.txt": six,
    }


CUR = date(2026, 7, 31)


def test_parse_symbols_cleans_and_dedups_comments():
    assert parse_symbols("# header\naapl\n\nNVDA\n") == ["AAPL", "NVDA"]


def test_parse_rejects_bad_symbol():
    with pytest.raises(ImportError_):
        parse_symbols("AA_PL")


def test_parse_rejects_intra_file_duplicate():
    with pytest.raises(ImportError_):
        parse_symbols("AAA\nAAA")


def test_happy_batch_and_candidate_sets():
    batch = build_batch(files(), current_market_date=CUR)
    assert batch.market_date == CUR
    assert batch.candidate_sets["intersection_3_of_3"] == ["CCC"]
    assert batch.candidate_sets["agreement_2_of_3"] == ["BBB", "CCC", "DDD"]
    assert len(batch.batch_hash) == 64
    assert batch.memberships["CCC"]["agreement_count"] == 3


def test_missing_scan_rejected():
    f = files()
    f.pop("strength_6m_2026-07-31.txt")
    with pytest.raises(ImportError_):
        build_batch(f, current_market_date=CUR)


def test_inconsistent_dates_rejected():
    f = {
        "strength_1m_2026-07-31.txt": "AAA",
        "strength_3m_2026-07-30.txt": "AAA",
        "strength_6m_2026-07-31.txt": "AAA",
    }
    with pytest.raises(ImportError_):
        build_batch(f, current_market_date=CUR)


def test_empty_list_rejected():
    f = files(one="# only comments\n")
    with pytest.raises(ImportError_):
        build_batch(f, current_market_date=CUR)


def test_stale_batch_rejected():
    f = files(d="2026-07-20")
    with pytest.raises(ImportError_):
        build_batch(f, current_market_date=CUR)


def test_future_dated_batch_rejected():
    f = files(d="2026-08-05")
    with pytest.raises(ImportError_):
        build_batch(f, current_market_date=CUR)


def test_bad_filename_rejected():
    with pytest.raises(ImportError_):
        build_batch({"scan1.txt": "AAA", "scan2.txt": "BBB", "scan3.txt": "CCC"},
                    current_market_date=CUR)
