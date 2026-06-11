"""Alembic environment.

The live schema is the declarative ``Base`` in ``app.db.engine`` with the table
classes in ``app.db.tables``. Fresh dev/test databases are still built by
``init_db()``/``create_all`` on boot; Alembic exists for evolving the
**production Supabase** schema, where ``create_all`` never alters existing
tables.

Production guard: the local ``.env`` points at production Supabase, so running
``alembic upgrade`` casually would hit prod. Any Postgres URL is refused unless
``CONFIRM_PROD=1`` is set. SQLite (rehearsals, tests) is always allowed.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import the app's metadata: engine.Base + the table classes registered on it.
from app.db.engine import Base, _normalize_url  # noqa: E402
import app.db.tables  # noqa: F401,E402  (register models on Base.metadata)

target_metadata = Base.metadata

db_url = _normalize_url(os.getenv("DATABASE_URL", "sqlite:///./lif.db"))

if not db_url.startswith("sqlite") and os.getenv("CONFIRM_PROD") != "1":
    raise SystemExit(
        "Refusing to run Alembic against a non-SQLite database "
        f"({db_url.split('@')[-1]}): this repo's DATABASE_URL points at "
        "PRODUCTION Supabase. Re-run with CONFIRM_PROD=1 only after following "
        "docs/migrations-runbook.md (pg_dump backup + --sql preview first)."
    )

config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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
