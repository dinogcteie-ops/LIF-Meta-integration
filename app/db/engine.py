"""SQLAlchemy engine / session factory for the relational backend.

Reads ``DATABASE_URL`` from settings. Defaults to a local SQLite file for dev and
tests; in production this points at Supabase Postgres (use the pooled connection
string, e.g. ``postgresql+psycopg2://...@...pooler.supabase.com:6543/postgres``).
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

Base = declarative_base()


def _normalize_url(url: str) -> str:
    # Supabase / Heroku style "postgres://" → SQLAlchemy "postgresql+psycopg2://"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_settings = get_settings()
_url = _normalize_url(_settings.database_url)

# SQLite needs check_same_thread=False for FastAPI's threadpool; Postgres wants
# pre-ping + modest pooling that plays nicely with Supabase's pgbouncer.
if _url.startswith("sqlite"):
    _engine_kwargs: dict = {"connect_args": {"check_same_thread": False}}
else:
    _engine_kwargs = {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 5}

engine = create_engine(_url, future=True, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables if they don't exist. Idempotent.

    Safe to call on startup; Alembic migrations can layer on top later for
    schema evolution, but create_all is enough to stand up a fresh database.
    """
    from app.db import tables  # noqa: F401  (register models on Base.metadata)
    Base.metadata.create_all(bind=engine)
