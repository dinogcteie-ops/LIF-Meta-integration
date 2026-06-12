"""Event delivery milestones.

Adds the ``event_milestones`` table backing the /delivery dashboard and the
per-event milestone checklist. Purely additive; fresh dev/test DBs get it from
create_all — this revision exists for production Supabase.

Revision ID: 0004_event_milestones
Revises: 0003_comm_log_capture
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_event_milestones"
down_revision: Union[str, None] = "0003_comm_log_capture"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_milestones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.Date(), nullable=True),
        sa.Column("assignee_payee_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_event_milestones_event_id", "event_milestones", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_milestones_event_id", table_name="event_milestones")
    op.drop_table("event_milestones")
