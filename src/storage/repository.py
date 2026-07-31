"""Typed repository over the sqlite storage.

Persists the auditable records and exposes the read queries the dashboard needs. Uniqueness
constraints enforce the safety invariants at the storage layer: one row per client_order_id
(idempotent orders) and one per transition idempotency_key (5R-once, duplicate-event safety).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .db import utcnow_iso


def _id() -> str:
    return str(uuid.uuid4())


class DuplicateKey(Exception):
    """Raised when an idempotent insert collides (client_order_id / idempotency_key)."""


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- TC2000 imports ------------------------------------------------------
    def record_import(self, *, market_date: str, batch_hash: str, status: str,
                      config_version: str, reject_reason: str | None = None) -> str:
        iid = _id()
        try:
            self.conn.execute(
                "INSERT INTO tc2000_imports(id, market_date, received_at, batch_hash, status,"
                " reject_reason, config_version) VALUES(?,?,?,?,?,?,?)",
                (iid, market_date, utcnow_iso(), batch_hash, status, reject_reason, config_version),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateKey(f"import batch_hash already recorded: {batch_hash}") from exc
        return iid

    def record_scan_file(self, *, import_id: str, scan: str, filename: str, file_hash: str,
                         symbol_count: int, raw_path: str | None = None) -> str:
        fid = _id()
        self.conn.execute(
            "INSERT INTO tc2000_scan_files(id, import_id, scan, filename, file_hash, raw_path,"
            " symbol_count) VALUES(?,?,?,?,?,?,?)",
            (fid, import_id, scan, filename, file_hash, raw_path, symbol_count),
        )
        self.conn.commit()
        return fid

    def record_membership(self, *, import_id: str, symbol: str, membership: dict,
                          candidate_sets: dict, composite_strength: float | None = None) -> str:
        mid = _id()
        self.conn.execute(
            "INSERT INTO candidate_membership(id, import_id, symbol, in_1m, in_3m, in_6m,"
            " agreement_count, mode_3of3, mode_2of3, mode_union, composite_strength, source)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?, 'TC2000')",
            (mid, import_id, symbol,
             int(bool(membership.get("one_month"))), int(bool(membership.get("three_month"))),
             int(bool(membership.get("six_month"))), int(membership.get("agreement_count", 0)),
             int(symbol in candidate_sets.get("intersection_3_of_3", [])),
             int(symbol in candidate_sets.get("agreement_2_of_3", [])),
             int(symbol in candidate_sets.get("union_ranked", [])),
             composite_strength),
        )
        self.conn.commit()
        return mid

    def latest_import(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tc2000_imports ORDER BY received_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def candidates_for_import(self, import_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM candidate_membership WHERE import_id=? ORDER BY symbol", (import_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- market snapshots + indicators --------------------------------------
    def record_snapshot(self, *, symbol: str, as_of: str, feed: str, bar_timestamp: str,
                        adjusted: bool, ohlcv: list[dict], staleness_seconds: float) -> str:
        sid = _id()
        self.conn.execute(
            "INSERT INTO market_snapshots(id, symbol, as_of, feed, bar_timestamp, adjusted,"
            " ohlcv_json, staleness_seconds) VALUES(?,?,?,?,?,?,?,?)",
            (sid, symbol, as_of, feed, bar_timestamp, int(adjusted),
             json.dumps(ohlcv), staleness_seconds),
        )
        self.conn.commit()
        return sid

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM market_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["ohlcv"] = json.loads(d["ohlcv_json"])
        return d

    def record_indicators(self, *, snapshot_id: str, symbol: str, as_of: str, timeframe: str,
                          values: dict) -> str:
        iid = _id()
        cols = ["sma10", "sma20", "sma50", "sma200", "vol_ema22", "adr_pct", "atr_pct",
                "dollar_volume", "slope10", "slope20", "slope50", "slope200"]
        self.conn.execute(
            "INSERT INTO indicator_values(id, snapshot_id, symbol, as_of, timeframe, "
            + ", ".join(cols) + ") VALUES(?,?,?,?,?," + ",".join("?" * len(cols)) + ")",
            (iid, snapshot_id, symbol, as_of, timeframe, *[values.get(c) for c in cols]),
        )
        self.conn.commit()
        return iid

    def get_indicators(self, snapshot_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM indicator_values WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- signals / orders / transitions (idempotent) ------------------------
    def record_signal(self, *, symbol: str, kind: str, state: str, accepted: bool,
                      idempotency_key: str, setup_id: str | None = None,
                      reject_reason: str | None = None) -> str:
        sid = _id()
        try:
            self.conn.execute(
                "INSERT INTO signals(id, symbol, setup_id, created_at, kind, state, accepted,"
                " reject_reason, idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, symbol, setup_id, utcnow_iso(), kind, state, int(accepted),
                 reject_reason, idempotency_key),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateKey(f"signal idempotency_key exists: {idempotency_key}") from exc
        return sid

    def record_order(self, *, client_order_id: str, symbol: str, side: str, order_type: str,
                     qty: int, signal_id: str | None = None, limit_price: float | None = None,
                     stop_price: float | None = None, status: str = "new",
                     broker_order_id: str | None = None) -> str:
        oid = _id()
        try:
            self.conn.execute(
                "INSERT INTO orders(id, signal_id, client_order_id, broker_order_id, symbol, side,"
                " type, limit_price, stop_price, qty, status, submitted_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, signal_id, client_order_id, broker_order_id, symbol, side, order_type,
                 limit_price, stop_price, qty, status, utcnow_iso()),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateKey(f"client_order_id exists: {client_order_id}") from exc
        return oid

    def record_transition(self, *, trade_id: str, from_state: str, to_state: str,
                          idempotency_key: str, reason: str, guard_results: dict) -> str | None:
        """Insert a state transition. Returns None if the idempotency_key already exists
        (duplicate event / restart replay) — a safe no-op."""
        tid = _id()
        try:
            self.conn.execute(
                "INSERT INTO position_state_transitions(id, trade_id, from_state, to_state,"
                " guard_results_json, idempotency_key, reason, at) VALUES(?,?,?,?,?,?,?,?)",
                (tid, trade_id, from_state, to_state, json.dumps(guard_results),
                 idempotency_key, reason, utcnow_iso()),
            )
            self.conn.commit()
            return tid
        except sqlite3.IntegrityError:
            return None

    def transitions_for(self, trade_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM position_state_transitions WHERE trade_id=? ORDER BY at", (trade_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- account snapshots + reconciliation ---------------------------------
    def record_daily_snapshot(self, *, session_date: str, equity: float, cash: float,
                              buying_power: float, committed_risk: float, exposure: float,
                              realized_pnl: float, unrealized_pnl: float, drawdown: float,
                              endpoint: str) -> str:
        did = _id()
        self.conn.execute(
            "INSERT INTO daily_account_snapshots(id, session_date, equity, cash, buying_power,"
            " committed_risk, exposure, realized_pnl, unrealized_pnl, drawdown, endpoint)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (did, session_date, equity, cash, buying_power, committed_risk, exposure,
             realized_pnl, unrealized_pnl, drawdown, endpoint),
        )
        self.conn.commit()
        return did

    def record_recon_incident(self, *, kind: str, symbol: str | None, detail: str,
                              broker_state: Any = None, db_state: Any = None) -> str:
        rid = _id()
        self.conn.execute(
            "INSERT INTO reconciliation_incidents(id, detected_at, kind, symbol, broker_state_json,"
            " db_state_json, detail, resolved) VALUES(?,?,?,?,?,?,?,0)",
            (rid, utcnow_iso(), kind, symbol,
             json.dumps(broker_state) if broker_state is not None else None,
             json.dumps(db_state) if db_state is not None else None, detail),
        )
        self.conn.commit()
        return rid

    def open_recon_incidents(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reconciliation_incidents WHERE resolved=0 ORDER BY detected_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def record_ai_review(self, *, subject_type: str, subject_id: str, model_name: str,
                         prompt_version: str, prompt: str, output: str,
                         tokens_in: int | None = None, tokens_out: int | None = None,
                         cost_usd: float | None = None) -> str:
        aid = _id()
        self.conn.execute(
            "INSERT INTO ai_reviews(id, subject_type, subject_id, model_name, prompt_version,"
            " prompt, output, tokens_in, tokens_out, cost_usd, at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (aid, subject_type, subject_id, model_name, prompt_version, prompt, output,
             tokens_in, tokens_out, cost_usd, utcnow_iso()),
        )
        self.conn.commit()
        return aid

    # -- dashboard read helpers ---------------------------------------------
    def open_orders(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM orders WHERE status IN "
            "('new','accepted','pending_new','partially_filled','held','replaced') ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_account_snapshot(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_account_snapshots ORDER BY session_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
