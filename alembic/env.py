"""Alembic environment — supports SQLite (dev) and PostgreSQL (production)."""
from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import all ORM models so Alembic can detect them for autogenerate
from app.api.models import Base  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url(url: str) -> str:
    """Convert async driver URLs to sync for Alembic's own runner."""
    url = re.sub(r"^sqlite\+aiosqlite", "sqlite", url)
    url = re.sub(r"^postgresql\+asyncpg", "postgresql", url)
    return url


def get_url() -> str:
    raw = os.environ.get("DATABASE_URL", "sqlite:///./trading.db")
    return _sync_url(raw)


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

