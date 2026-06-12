"""SQLAlchemy ORM tables mirroring the domain dataclasses.

Columns are deliberately close to the old Google-Sheets headers so the migration
maps 1:1. Enum-typed domain fields (scope/status/payment_status) are stored as
plain strings and converted at the dataclass boundary. ``created_at`` stays a
string (ISO timestamp) to match the dataclass contract.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Date, Float, Integer, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.engine import Base


class CategoryRow(Base):
    __tablename__ = "expense_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    monthly_budget: Mapped[float] = mapped_column(Float, default=0.0)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    client_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_date: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    quoted_amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="active")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    referral_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_due_dates: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reminder_sent: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    reminder_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PaymentRow(Base):
    __tablename__ = "event_payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    payment_date: Mapped["Date"] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")


class ExpenseRow(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped["Date"] = mapped_column(Date)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    category_id: Mapped[int] = mapped_column(Integer, index=True)
    scope: Mapped[str] = mapped_column(String(32), default="event")
    payment_status: Mapped[str] = mapped_column(String(32), default="paid")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    paid_amount: Mapped[float] = mapped_column(Float, default=0.0)
    paid_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    payee_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurring_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ClientRow(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    # Repeat-business dates (anniversary shoots, birthday campaigns):
    birthday: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    anniversary: Mapped["Date | None"] = mapped_column(Date, nullable=True)


class PayeeRow(Base):
    __tablename__ = "payees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    payee_type: Mapped[str] = mapped_column(String(32), default="freelancer")
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")


class SettingRow(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[str] = mapped_column(String(64), default="")
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text, default="")


class LeadRow(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_name: Mapped[str] = mapped_column(String(512), default="")
    contact: Mapped[str] = mapped_column(String(255), default="")
    event_type: Mapped[str] = mapped_column(String(64), default="")
    tentative_date: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="new")
    quoted_amount: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_events: Mapped[int] = mapped_column(Integer, default=0)
    revised_quote: Mapped[float] = mapped_column(Float, default=0.0)
    follow_ups: Mapped[str] = mapped_column(Text, default="")
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    meta_campaign: Mapped[bool] = mapped_column(Boolean, default=False)
    referral_name: Mapped[str] = mapped_column(String(255), default="")
    followup_status: Mapped[str] = mapped_column(String(32), default="pending")
    followup_date: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    # Meta Lead Ads provenance:
    meta_lead_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    meta_campaign_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_form_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Structured capture (promoted out of free-text notes — prime triage signals):
    budget_range: Mapped[str] = mapped_column(String(128), default="")
    city: Mapped[str] = mapped_column(String(128), default="")
    # AI triage: hot | warm | low_intent | spam ("" = not yet triaged).
    triage: Mapped[str] = mapped_column(String(16), default="")
    triage_source: Mapped[str] = mapped_column(String(16), default="")   # llm | ml | manual
    triage_reason: Mapped[str] = mapped_column(Text, default="")
    triaged_at: Mapped[str] = mapped_column(String(64), default="")
    # First outbound touch (ISO timestamp) — set by the communication log;
    # response time is the strongest conversion signal for the future ML model.
    first_response_at: Mapped[str] = mapped_column(String(64), default="")


class CommLogRow(Base):
    __tablename__ = "communication_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16), index=True)   # lead | client | event
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")  # whatsapp|email|call|meeting|other
    direction: Mapped[str] = mapped_column(String(8), default="out")      # out | in
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class MilestoneRow(Base):
    __tablename__ = "event_milestones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    phase: Mapped[str] = mapped_column(String(128))
    position: Mapped[int] = mapped_column(Integer, default=0)
    due_date: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    completed_at: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    assignee_payee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class MetaMetricRow(Base):
    __tablename__ = "meta_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    campaign_name: Mapped[str] = mapped_column(String(255), default="")
    date: Mapped["Date"] = mapped_column(Date, index=True)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    leads: Mapped[int] = mapped_column(Integer, default=0)
    cpl: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="")
    fetched_at: Mapped[str] = mapped_column(String(64), default="")
