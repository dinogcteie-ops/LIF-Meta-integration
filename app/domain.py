"""Domain dataclasses — the boundary objects returned by the data layer.

These are intentionally plain dataclasses (not ORM rows). Both the new Postgres
data layer (``app.services.db``) and the one-time Sheets migration script return
these exact types, so routes and Jinja templates are agnostic to the storage
backend. This is what makes the Google-Sheets → Postgres swap a drop-in change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.enums import CategoryScope, EventStatus, PaymentStatus


@dataclass
class ExpenseCategory:
    id: int
    name: str
    scope: CategoryScope
    is_active: bool = True
    created_at: str = ""
    monthly_budget: float = 0.0


@dataclass
class Event:
    id: int
    name: str
    client_name: Optional[str] = None
    event_date: Optional[date] = None
    quoted_amount: float = 0.0
    status: EventStatus = EventStatus.active
    notes: Optional[str] = None
    created_at: str = ""
    event_type: Optional[str] = None
    location: Optional[str] = None
    referral_source: Optional[str] = None
    # Cash-flow:
    payment_due_dates: Optional[str] = None   # JSON list of ISO dates
    last_reminder_sent: Optional[date] = None
    reminder_notes: Optional[str] = None
    # Directory FK:
    client_id: Optional[int] = None
    # Delivery workflow:
    delivery_status: Optional[str] = None     # shooting_done | editing | review | delivered
    payments: list["EventPayment"] = field(default_factory=list, compare=False, repr=False)
    expenses: list["Expense"] = field(default_factory=list, compare=False, repr=False)


@dataclass
class EventPayment:
    id: int
    event_id: int
    amount: float
    payment_date: date
    notes: Optional[str] = None
    created_at: str = ""
    event: Optional[Event] = field(default=None, compare=False, repr=False)


@dataclass
class Expense:
    id: int
    date: date
    category_id: int
    scope: CategoryScope
    payment_status: PaymentStatus
    amount: float
    event_id: Optional[int] = None
    paid_amount: float = 0.0
    paid_to: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""
    payee_id: Optional[int] = None
    is_recurring: bool = False
    recurring_day: Optional[int] = None
    payment_type: Optional[str] = None     # Cash/Credit/UPI/Current A/C/Savings A/C
    category: Optional[ExpenseCategory] = field(default=None, compare=False, repr=False)
    event: Optional[Event] = field(default=None, compare=False, repr=False)


@dataclass
class Client:
    """A repeat client / contact associated with one or more events."""
    id: int
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""
    # Repeat-business dates (year matters for age only; campaigns match month/day):
    birthday: Optional[date] = None
    anniversary: Optional[date] = None


@dataclass
class CommLog:
    """One logged touch with a lead/client/event — call, WhatsApp, email…"""
    id: int
    entity_type: str          # lead | client | event
    entity_id: int
    channel: str = "whatsapp" # whatsapp | email | call | meeting | other
    direction: str = "out"    # out | in
    summary: str = ""
    created_at: str = ""


@dataclass
class Payee:
    """A freelancer, vendor, or other party that the studio pays."""
    id: int
    name: str
    payee_type: str = "freelancer"   # freelancer | vendor | other
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""


@dataclass
class AuditEntry:
    """One row in the audit log."""
    id: int
    timestamp: str
    entity_type: str    # event | payment | expense | client | payee | lead
    entity_id: int
    action: str         # create | update | delete
    summary: str


@dataclass
class Lead:
    """A pre-booking enquiry / lead."""
    id: int
    client_name: str
    contact: str = ""
    event_type: str = ""
    tentative_date: Optional[date] = None
    source: str = ""
    status: str = "new"      # new | quoted | won | lost | cold
    quoted_amount: float = 0.0
    notes: str = ""
    created_at: str = ""
    client_id: Optional[int] = None
    num_events: int = 0
    revised_quote: float = 0.0
    follow_ups: str = ""
    rejection_reason: str = ""
    meta_campaign: bool = False
    referral_name: str = ""
    followup_status: str = "pending"        # pending | scheduled | done
    followup_date: Optional[date] = None
    # Meta Lead Ads provenance (nullable — only set for ads-sourced leads):
    meta_lead_id: Optional[str] = None
    meta_campaign_name: Optional[str] = None
    meta_form_id: Optional[str] = None
    # Structured capture (promoted out of free-text notes):
    budget_range: str = ""
    city: str = ""
    # AI triage: "" (untriaged) | hot | warm | low_intent | spam
    triage: str = ""
    triage_source: str = ""      # llm | ml | manual
    triage_reason: str = ""
    triaged_at: str = ""
    # First outbound touch (ISO timestamp; "" = not contacted yet):
    first_response_at: str = ""


@dataclass
class Milestone:
    """One post-production phase milestone for an event."""
    id: int
    event_id: int
    phase: str
    position: int = 0
    due_date: Optional[date] = None
    completed_at: Optional[date] = None
    assignee_payee_id: Optional[int] = None
    notes: str = ""


@dataclass
class MetaMetric:
    """One campaign's metrics for a single day, pulled from the Meta Insights API."""
    id: int
    campaign_id: str
    campaign_name: str
    date: date
    spend: float = 0.0
    impressions: int = 0
    reach: int = 0
    clicks: int = 0
    leads: int = 0
    cpl: float = 0.0          # cost per lead
    currency: str = ""
    fetched_at: str = ""
