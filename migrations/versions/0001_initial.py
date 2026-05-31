"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "expense_category",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, index=True),
        sa.Column("scope", sa.String, nullable=False, server_default="event"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "event",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, index=True),
        sa.Column("client_name", sa.String, nullable=True),
        sa.Column("event_date", sa.Date, nullable=True),
        sa.Column("quoted_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("notes", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "event_payment",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("event.id"), nullable=False, index=True),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("payment_date", sa.Date, nullable=False),
        sa.Column("notes", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "expense",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("event.id"), nullable=True, index=True),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("expense_category.id"), nullable=False, index=True),
        sa.Column("scope", sa.String, nullable=False, server_default="event"),
        sa.Column("payment_status", sa.String, nullable=False, server_default="paid"),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("paid_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("paid_to", sa.String, nullable=True),
        sa.Column("notes", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("expense")
    op.drop_table("event_payment")
    op.drop_table("event")
    op.drop_table("expense_category")
