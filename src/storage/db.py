"""Stdlib sqlite3 database bootstrap for dev/tests.

Deployment uses PostgreSQL via the SQLAlchemy models + Alembic migration (see
src/storage/models.py and migrations/). This module is the dependency-light path that the
deterministic test-suite and local development use; it applies the same canonical DDL
(schema.sql) so both backends share one schema of record.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open a connection with foreign keys on and row access by name."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply the canonical schema (idempotent — CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
