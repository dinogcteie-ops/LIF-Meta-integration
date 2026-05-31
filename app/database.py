"""Database dependency — backed by Supabase Postgres (SQLAlchemy).

``SheetDB`` is kept as an alias of ``Database`` so existing route imports
(``from app.database import get_db, SheetDB``) keep working unchanged.
"""
from app.services.db import Database, get_db

SheetDB = Database  # backwards-compatible alias for route type hints

__all__ = ["Database", "SheetDB", "get_db"]
