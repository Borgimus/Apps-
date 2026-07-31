"""SQLAlchemy deployment models — validated when the dependency is present.

Skipped in the dependency-light CI (stdlib + pyyaml). This guards the ORM models against
drift from the canonical schema (src/storage/schema.sql) wherever SQLAlchemy is installed.
"""
import pytest

pytest.importorskip("sqlalchemy")

from src.storage.models import Base  # noqa: E402


def test_models_declare_expected_tables():
    tables = set(Base.metadata.tables)
    expected = {
        "tc2000_imports", "signals", "orders", "position_state_transitions",
        "daily_account_snapshots", "reconciliation_incidents",
    }
    assert expected <= tables


def test_idempotency_columns_are_unique():
    orders = Base.metadata.tables["orders"]
    assert orders.c.client_order_id.unique is True
    trans = Base.metadata.tables["position_state_transitions"]
    assert trans.c.idempotency_key.unique is True
