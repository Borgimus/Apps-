"""
Tests for the health, heartbeat, dashboard, and pending-orders API endpoints,
plus logging initialisation.

Scope:
- GET /health — 200, structure, paper_mode, DB check, market status, heartbeat
- GET /          — dashboard HTML served (200) or 404 if static not built
- GET /pending-orders — returns list (empty if no rows)
- configure_logging — handlers created, idempotent
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Minimal settings mock so import of dashboard_api doesn't hit disk ─────────

@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    """
    Provide a deterministic settings object so tests don't depend on .env or
    config.yaml.  Patch both get_settings() call sites.
    """
    s = MagicMock()
    s.live_trading_enabled = False
    s.broker = "alpaca"
    s.database_url = "sqlite+aiosqlite:///:memory:"
    s.log_level = "INFO"
    s.log_file = "./logs/trading.log"
    s.kill_switch_file = "./KILL_SWITCH_TEST_NONEXISTENT"
    s.is_kill_switch_active.return_value = False
    s.market_open = "09:30"
    s.market_close = "16:00"

    monkeypatch.setattr("app.config.get_settings", lambda: s)
    monkeypatch.setattr("app.api.models.get_settings", lambda: s, raising=False)
    monkeypatch.setattr("app.api.dashboard_api.get_settings", lambda: s, raising=False)
    return s


# ── In-memory DB fixture ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def memory_engine():
    from app.api.models import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def memory_session(memory_engine):
    factory = async_sessionmaker(memory_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(memory_engine):
    """
    Build the FastAPI app and override get_db to use the in-memory engine.
    """
    from app.api.dashboard_api import create_app
    from app.api.models import get_db, Base

    app = create_app()

    factory = async_sessionmaker(memory_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_has_required_keys(self, client):
        data = (await client.get("/health")).json()
        for key in ("status", "paper_mode", "kill_switch_active", "database", "runner", "market"):
            assert key in data, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_paper_mode_always_true(self, client):
        data = (await client.get("/health")).json()
        assert data["paper_mode"] is True

    @pytest.mark.asyncio
    async def test_database_ok_when_db_accessible(self, client):
        data = (await client.get("/health")).json()
        assert data["database"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_runner_not_active_with_no_heartbeat(self, client):
        data = (await client.get("/health")).json()
        assert data["runner"]["active"] is False
        assert data["runner"]["heartbeat_age_secs"] is None

    @pytest.mark.asyncio
    async def test_kill_switch_reflects_settings(self, client, _mock_settings):
        _mock_settings.is_kill_switch_active.return_value = True
        data = (await client.get("/health")).json()
        assert data["kill_switch_active"] is True

    @pytest.mark.asyncio
    async def test_market_status_open_during_hours(self, client):
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        fake_now = datetime(2024, 3, 18, 10, 30, 0, tzinfo=ET)  # Monday 10:30 ET
        with patch("app.api.dashboard_api.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            data = (await client.get("/health")).json()
        assert data["market"]["status"] == "open"

    @pytest.mark.asyncio
    async def test_market_status_pre_market(self, client):
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        fake_now = datetime(2024, 3, 18, 8, 0, 0, tzinfo=ET)  # Monday 08:00 ET
        with patch("app.api.dashboard_api.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            data = (await client.get("/health")).json()
        assert data["market"]["status"] == "pre_market"

    @pytest.mark.asyncio
    async def test_market_status_after_hours(self, client):
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        fake_now = datetime(2024, 3, 18, 17, 0, 0, tzinfo=ET)  # Monday 17:00 ET
        with patch("app.api.dashboard_api.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            data = (await client.get("/health")).json()
        assert data["market"]["status"] == "after_hours"

    @pytest.mark.asyncio
    async def test_market_status_weekend(self, client):
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        fake_now = datetime(2024, 3, 16, 11, 0, 0, tzinfo=ET)  # Saturday 11:00 ET
        with patch("app.api.dashboard_api.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            data = (await client.get("/health")).json()
        assert data["market"]["status"] == "weekend"


# ── Runner heartbeat from DB ──────────────────────────────────────────────────

class TestRunnerHeartbeat:
    @pytest.mark.asyncio
    async def test_runner_active_after_recent_heartbeat(self, memory_engine, _mock_settings):
        from app.api.dashboard_api import create_app
        from app.api.models import get_db, DBSessionLog
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        app = create_app()
        factory = async_sessionmaker(memory_engine, expire_on_commit=False)

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db

        # Seed a heartbeat 30 seconds ago
        now = datetime.now(tz=ET)
        async with factory() as session:
            log = DBSessionLog(
                session_date=str(date.today()),
                timestamp=now,
                event="heartbeat",
                message="cycle=1",
                level="info",
            )
            session.add(log)
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            data = (await ac.get("/health")).json()

        assert data["runner"]["active"] is True
        assert data["runner"]["heartbeat_age_secs"] is not None
        assert data["runner"]["heartbeat_age_secs"] < 60


# ── Dashboard UI ──────────────────────────────────────────────────────────────

class TestDashboardUI:
    @pytest.mark.asyncio
    async def test_root_returns_html(self, client):
        resp = await client.get("/")
        assert resp.status_code in (200, 404)
        content_type = resp.headers.get("content-type", "")
        assert "text/html" in content_type

    @pytest.mark.asyncio
    async def test_root_returns_200_if_static_exists(self, client):
        static_path = Path(__file__).parent.parent / "app" / "static" / "index.html"
        if not static_path.exists():
            pytest.skip("index.html not built")
        resp = await client.get("/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_root_contains_dashboard_markup(self, client):
        static_path = Path(__file__).parent.parent / "app" / "static" / "index.html"
        if not static_path.exists():
            pytest.skip("index.html not built")
        resp = await client.get("/")
        assert "<html" in resp.text.lower() or "<!doctype" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_root_returns_404_markup_if_static_missing(self):
        from app.api.dashboard_api import create_app
        from app.api.models import get_db
        from sqlalchemy.ext.asyncio import AsyncSession

        app = create_app()

        async def fake_db():
            yield MagicMock(spec=AsyncSession)

        app.dependency_overrides[get_db] = fake_db

        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/")
        assert resp.status_code == 404


# ── /pending-orders ───────────────────────────────────────────────────────────

class TestPendingOrdersEndpoint:
    @pytest.mark.asyncio
    async def test_pending_orders_empty(self, client):
        resp = await client.get("/pending-orders")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_pending_orders_returns_todays_rows(self, memory_engine, _mock_settings):
        from app.api.dashboard_api import create_app
        from app.api.models import get_db, DBPendingOrder

        app = create_app()
        factory = async_sessionmaker(memory_engine, expire_on_commit=False)

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db

        today = str(date.today())
        async with factory() as session:
            row = DBPendingOrder(
                order_id="order-abc-123",
                option_symbol="SPY240115C00450000",
                symbol="SPY",
                strategy_id="orb",
                direction="long",
                quantity=1,
                limit_price=3.05,
                submitted_at=datetime.utcnow(),
                status="pending",
                session_date=today,
            )
            session.add(row)
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/pending-orders")

        assert resp.status_code == 200
        orders = resp.json()
        assert len(orders) == 1
        assert orders[0]["order_id"] == "order-abc-123"
        assert orders[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_pending_orders_filtered_by_session_date(self, memory_engine, _mock_settings):
        from app.api.dashboard_api import create_app
        from app.api.models import get_db, DBPendingOrder

        app = create_app()
        factory = async_sessionmaker(memory_engine, expire_on_commit=False)

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db

        async with factory() as session:
            session.add(DBPendingOrder(
                order_id="old-order-1",
                option_symbol="SPY240101C00450000",
                symbol="SPY",
                strategy_id="orb",
                direction="long",
                quantity=1,
                limit_price=3.00,
                submitted_at=datetime.utcnow(),
                status="pending",
                session_date="2024-01-01",
            ))
            session.add(DBPendingOrder(
                order_id="today-order-1",
                option_symbol="SPY240115C00450000",
                symbol="SPY",
                strategy_id="orb",
                direction="long",
                quantity=1,
                limit_price=3.05,
                submitted_at=datetime.utcnow(),
                status="pending",
                session_date=str(date.today()),
            ))
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/pending-orders")

        ids = [o["order_id"] for o in resp.json()]
        assert "today-order-1" in ids
        assert "old-order-1" not in ids


# ── Logging setup ─────────────────────────────────────────────────────────────

class TestLoggingSetup:
    def test_configure_logging_creates_handlers(self, tmp_path):
        root = logging.getLogger()
        root.handlers.clear()

        from app.utils.logging_setup import configure_logging
        # log_dir is treated as a file path; .parent is the directory
        configure_logging(log_dir=str(tmp_path / "trading.log"))

        assert len(root.handlers) > 0

    def test_configure_logging_creates_log_files(self, tmp_path):
        root = logging.getLogger()
        root.handlers.clear()

        from app.utils.logging_setup import configure_logging
        configure_logging(log_dir=str(tmp_path / "trading.log"))

        # Trigger a write so the RotatingFileHandler creates the file
        logging.getLogger("test_health").info("init write")

        log_files = list(tmp_path.iterdir())
        names = {f.name for f in log_files}
        assert any("trading" in n for n in names), f"No trading.log in {names}"

    def test_configure_logging_is_idempotent(self, tmp_path):
        root = logging.getLogger()
        root.handlers.clear()

        from app.utils.logging_setup import configure_logging
        configure_logging(log_dir=str(tmp_path / "trading.log"))
        count_after_first = len(root.handlers)
        configure_logging(log_dir=str(tmp_path / "trading.log"))
        count_after_second = len(root.handlers)

        assert count_after_first == count_after_second

    def test_configure_logging_creates_errors_log(self, tmp_path):
        root = logging.getLogger()
        root.handlers.clear()

        from app.utils.logging_setup import configure_logging
        configure_logging(log_dir=str(tmp_path / "trading.log"))

        # Trigger an error so the file gets created
        logging.getLogger("test").error("forced error for test")
        errors_log = tmp_path / "errors.log"
        assert errors_log.exists()

    def test_configure_logging_json_handler_present(self, tmp_path):
        root = logging.getLogger()
        root.handlers.clear()

        from app.utils.logging_setup import configure_logging, _JsonFormatter
        configure_logging(log_dir=str(tmp_path / "trading.log"))

        json_handlers = [
            h for h in root.handlers
            if isinstance(h.formatter, _JsonFormatter)
        ]
        assert len(json_handlers) >= 1

    def test_noisy_loggers_suppressed(self, tmp_path):
        root = logging.getLogger()
        root.handlers.clear()

        from app.utils.logging_setup import configure_logging
        configure_logging(log_dir=str(tmp_path / "trading.log"))

        for name in ("yfinance", "urllib3", "httpcore"):
            lvl = logging.getLogger(name).level
            assert lvl >= logging.WARNING, f"{name} level should be WARNING+, got {lvl}"
