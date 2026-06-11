"""Lead AI-triage + structured capture columns.

Adds to ``leads``: budget_range, city (promoted out of free-text notes by the
Sheet intake) and triage/triage_source/triage_reason/triaged_at (AI verdicts).
Purely additive; existing rows get empty-string defaults, matching how the
ORM treats "" as "not set" for these fields. Fresh dev/test DBs get the same
columns from create_all — this revision exists for production Supabase.

Revision ID: 0002_lead_triage_capture
Revises: 0001_baseline
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_lead_triage_capture"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("budget_range", sa.String(128),
                                     nullable=False, server_default=""))
    op.add_column("leads", sa.Column("city", sa.String(128),
                                     nullable=False, server_default=""))
    op.add_column("leads", sa.Column("triage", sa.String(16),
                                     nullable=False, server_default=""))
    op.add_column("leads", sa.Column("triage_source", sa.String(16),
                                     nullable=False, server_default=""))
    op.add_column("leads", sa.Column("triage_reason", sa.Text(),
                                     nullable=False, server_default=""))
    op.add_column("leads", sa.Column("triaged_at", sa.String(64),
                                     nullable=False, server_default=""))


def downgrade() -> None:
    for col in ("triaged_at", "triage_reason", "triage_source", "triage",
                "city", "budget_range"):
        op.drop_column("leads", col)
