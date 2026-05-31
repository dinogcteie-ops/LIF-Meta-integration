"""Test fixtures for the Postgres/SQLAlchemy-backed app.

Each test session gets its own temporary SQLite database file (the data layer is
storage-agnostic, so SQLite exercises the same ``Database`` code paths used
against Supabase Postgres in production).
"""
import os
import tempfile

import pytest

# Configure environment *before* importing the app/config (settings are cached).
_TMP_DB = os.path.join(tempfile.gettempdir(), "lif_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["APP_PASSWORD"] = "testpass"
os.environ["SECRET_KEY"] = "test-secret-key-long-enough-for-signing"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.engine import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.db import get_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_db():
    init_db()
    get_db().seed_if_empty()
    yield


@pytest.fixture()
def db():
    return get_db()


@pytest.fixture()
def client():
    """An authenticated TestClient (logged in as the owner)."""
    with TestClient(app, follow_redirects=True) as c:
        c.post("/login", data={"password": "testpass"})
        yield c
