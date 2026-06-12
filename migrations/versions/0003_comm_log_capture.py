"""Communication log + response-time + client milestone dates.

Adds the ``communication_log`` table (touch history for leads/clients/events),
``leads.first_response_at`` (stamped by the first outbound touch — the key
response-time signal for the future ML model) and ``clients.birthday`` /
``clients.anniversary`` (repeat-business campaign dates). Purely additive;
fresh dev/test DBs get all of it from create_all — this revision exists for
production Supabase.

Revision ID: 0003_comm_log_capture
Revises: 0002_lead_triage_capture
Create Date: 2026-06-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_comm_log_capture"
down_revision: Union[str, None] = "0002_lead_triage_capture"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "communication_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="whatsapp"),
        sa.Column("direction", sa.String(8), nullable=False, server_default="out"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index("ix_communication_log_entity_type", "communication_log", ["entity_type"])
    op.create_index("ix_communication_log_entity_id", "communication_log", ["entity_id"])
    op.add_column("leads", sa.Column("first_response_at", sa.String(64),
                                     nullable=False, server_default=""))
    op.add_column("clients", sa.Column("birthday", sa.Date(), nullable=True))
    op.add_column("clients", sa.Column("anniversary", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "anniversary")
    op.drop_column("clients", "birthday")
    op.drop_column("leads", "first_response_at")
    op.drop_index("ix_communication_log_entity_id", table_name="communication_log")
    op.drop_index("ix_communication_log_entity_type", table_name="communication_log")
    op.drop_table("communication_log")
