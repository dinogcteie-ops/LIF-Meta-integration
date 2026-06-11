"""Baseline — marks the schema as it exists in production today.

This revision is intentionally a no-op: the current schema (10 tables, see
``app/db/tables.py``) was created by ``Base.metadata.create_all`` on boot, not
by Alembic. Production is brought under Alembic control by stamping, never by
running this revision:

    CONFIRM_PROD=1 alembic stamp 0001_baseline

Fresh dev/test databases keep getting the FULL current schema from
``create_all`` at startup; after that, ``alembic stamp 0001_baseline`` +
``alembic upgrade head`` applies only the later revisions. New tables added to
``app/db/tables.py`` appear in fresh DBs automatically via ``create_all`` but
MUST also get a revision here so production receives them.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-12
"""
from typing import Sequence, Union

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: schema already exists (created by create_all). See module docstring.
    pass


def downgrade() -> None:
    pass
