"""Storage repository: persistence, read-back, and idempotency constraints."""
import pytest

from src.storage.db import connect, init_db
from src.storage.repository import DuplicateKey, Repository


@pytest.fixture
def repo():
    conn = connect(":memory:")
    init_db(conn)
    return Repository(conn)


def test_schema_applies_and_import_roundtrip(repo):
    iid = repo.record_import(market_date="2026-07-31", batch_hash="abc123",
                             status="ACCEPTED", config_version="1.0.0")
    repo.record_scan_file(import_id=iid, scan="one_month", filename="strength_1m_2026-07-31.txt",
                          file_hash="h1", symbol_count=3)
    repo.record_membership(import_id=iid, symbol="SMCI",
                           membership={"one_month": True, "three_month": True, "six_month": True,
                                       "agreement_count": 3},
                           candidate_sets={"intersection_3_of_3": ["SMCI"],
                                           "agreement_2_of_3": ["SMCI"], "union_ranked": ["SMCI"]})
    latest = repo.latest_import()
    assert latest["batch_hash"] == "abc123" and latest["status"] == "ACCEPTED"
    cands = repo.candidates_for_import(iid)
    assert cands[0]["symbol"] == "SMCI" and cands[0]["mode_3of3"] == 1


def test_duplicate_batch_hash_rejected(repo):
    repo.record_import(market_date="2026-07-31", batch_hash="dup",
                       status="ACCEPTED", config_version="1.0.0")
    with pytest.raises(DuplicateKey):
        repo.record_import(market_date="2026-07-31", batch_hash="dup",
                           status="ACCEPTED", config_version="1.0.0")


def test_client_order_id_unique(repo):
    repo.record_order(client_order_id="entry-1", symbol="AAA", side="buy",
                      order_type="stop_limit", qty=100, stop_price=50, limit_price=50.5)
    with pytest.raises(DuplicateKey):
        repo.record_order(client_order_id="entry-1", symbol="AAA", side="buy",
                          order_type="stop_limit", qty=100, stop_price=50, limit_price=50.5)


def test_transition_idempotency_key_is_noop_on_duplicate(repo):
    first = repo.record_transition(trade_id="T1", from_state="IMPORTED", to_state="QUALIFIED",
                                   idempotency_key="k1", reason="q", guard_results={"_ok": True})
    dup = repo.record_transition(trade_id="T1", from_state="IMPORTED", to_state="QUALIFIED",
                                 idempotency_key="k1", reason="q-again", guard_results={})
    assert first is not None and dup is None            # duplicate is a safe no-op
    assert len(repo.transitions_for("T1")) == 1


def test_recon_incident_and_open_orders(repo):
    repo.record_recon_incident(kind="MISSING_STOP", symbol="AAA", detail="no stop")
    assert len(repo.open_recon_incidents()) == 1
    repo.record_order(client_order_id="o1", symbol="AAA", side="buy", order_type="limit",
                      qty=10, status="accepted", limit_price=10)
    assert len(repo.open_orders()) == 1


def test_daily_snapshot_records_paper_endpoint(repo):
    repo.record_daily_snapshot(session_date="2026-07-31", equity=100000, cash=100000,
                               buying_power=200000, committed_risk=500, exposure=20000,
                               realized_pnl=0, unrealized_pnl=0, drawdown=0,
                               endpoint="https://paper-api.alpaca.markets")
    snap = repo.latest_account_snapshot()
    assert snap["endpoint"] == "https://paper-api.alpaca.markets"
