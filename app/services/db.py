"""Relational data layer (Supabase Postgres / SQLite).

``Database`` is a drop-in replacement for the old ``SheetDB``: identical public
methods, identical return types (the dataclasses in ``app.domain``). Routes and
templates are unchanged. ORM rows never escape this module — every method
converts to/from domain dataclasses at the boundary.
"""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Optional

from sqlalchemy import select

from app.db.engine import SessionLocal
from app.db.tables import (
    AuditRow, CategoryRow, ClientRow, EventRow, ExpenseRow, LeadRow,
    MetaMetricRow, PayeeRow, PaymentRow, SettingRow,
)
from app.domain import (
    AuditEntry, Client, Event, EventPayment, Expense, ExpenseCategory, Lead,
    MetaMetric, Payee,
)
from app.enums import CategoryScope, EventStatus, PaymentStatus

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.utcnow().isoformat()


# ─── ORM → domain mappers ────────────────────────────────────────────────────

def _cat(r: CategoryRow) -> ExpenseCategory:
    return ExpenseCategory(
        id=r.id, name=r.name, scope=CategoryScope(r.scope),
        is_active=bool(r.is_active), created_at=r.created_at or "",
        monthly_budget=r.monthly_budget or 0.0,
    )


def _event(r: EventRow) -> Event:
    return Event(
        id=r.id, name=r.name, client_name=r.client_name or None,
        event_date=r.event_date, quoted_amount=r.quoted_amount or 0.0,
        status=EventStatus(r.status or "active"), notes=r.notes or None,
        created_at=r.created_at or "", event_type=r.event_type or None,
        location=r.location or None, referral_source=r.referral_source or None,
        payment_due_dates=r.payment_due_dates or None,
        last_reminder_sent=r.last_reminder_sent,
        reminder_notes=r.reminder_notes or None,
        client_id=r.client_id, delivery_status=r.delivery_status or None,
    )


def _payment(r: PaymentRow) -> EventPayment:
    return EventPayment(
        id=r.id, event_id=r.event_id, amount=r.amount or 0.0,
        payment_date=r.payment_date or date.today(),
        notes=r.notes or None, created_at=r.created_at or "",
    )


def _expense(r: ExpenseRow) -> Expense:
    return Expense(
        id=r.id, date=r.date or date.today(), event_id=r.event_id,
        category_id=r.category_id, scope=CategoryScope(r.scope or "event"),
        payment_status=PaymentStatus(r.payment_status or "paid"),
        amount=r.amount or 0.0, paid_amount=r.paid_amount or 0.0,
        paid_to=r.paid_to or None, notes=r.notes or None,
        created_at=r.created_at or "", payee_id=r.payee_id,
        is_recurring=bool(r.is_recurring), recurring_day=r.recurring_day,
        payment_type=r.payment_type or None,
    )


def _client(r: ClientRow) -> Client:
    return Client(id=r.id, name=r.name, phone=r.phone or None, email=r.email or None,
                  address=r.address or None, notes=r.notes or None,
                  created_at=r.created_at or "")


def _payee(r: PayeeRow) -> Payee:
    return Payee(id=r.id, name=r.name, payee_type=r.payee_type or "freelancer",
                 phone=r.phone or None, email=r.email or None, notes=r.notes or None,
                 created_at=r.created_at or "")


def _audit_entry(r: AuditRow) -> AuditEntry:
    return AuditEntry(id=r.id, timestamp=r.timestamp or "", entity_type=r.entity_type or "",
                      entity_id=r.entity_id or 0, action=r.action or "", summary=r.summary or "")


def _lead(r: LeadRow) -> Lead:
    return Lead(
        id=r.id, client_name=r.client_name or "", contact=r.contact or "",
        event_type=r.event_type or "", tentative_date=r.tentative_date,
        source=r.source or "", status=r.status or "new",
        quoted_amount=r.quoted_amount or 0.0, notes=r.notes or "",
        created_at=r.created_at or "", client_id=r.client_id,
        num_events=r.num_events or 0, revised_quote=r.revised_quote or 0.0,
        follow_ups=r.follow_ups or "", rejection_reason=r.rejection_reason or "",
        meta_campaign=bool(r.meta_campaign), referral_name=r.referral_name or "",
        followup_status=r.followup_status or "pending", followup_date=r.followup_date,
        meta_lead_id=r.meta_lead_id, meta_campaign_name=r.meta_campaign_name,
        meta_form_id=r.meta_form_id,
    )


def _metric(r: MetaMetricRow) -> MetaMetric:
    return MetaMetric(
        id=r.id, campaign_id=r.campaign_id, campaign_name=r.campaign_name or "",
        date=r.date, spend=r.spend or 0.0, impressions=r.impressions or 0,
        reach=r.reach or 0, clicks=r.clicks or 0, leads=r.leads or 0,
        cpl=r.cpl or 0.0, currency=r.currency or "", fetched_at=r.fetched_at or "",
    )


# ─── Database ────────────────────────────────────────────────────────────────

def _request_cached(fn):
    """Memoize a read method for the duration of a request, when caching is enabled.

    Keyed on (method name, args, kwargs). Inert unless ``enable_cache()`` was called
    (so normal mutating flows are never cached). Used to collapse the dashboard's many
    repeated table reads into one round-trip each.
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        cache = getattr(self, "_cache", None)
        if cache is None:
            return fn(self, *args, **kwargs)
        key = (fn.__name__, args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = fn(self, *args, **kwargs)
        return cache[key]
    return wrapper


class Database:
    """Postgres/SQLite-backed implementation of the LIF data layer."""

    _cache: "dict | None" = None   # per-request read cache; None = disabled

    def enable_cache(self) -> None:
        """Start caching read methods for this request (call disable_cache() after)."""
        self._cache = {}

    def disable_cache(self) -> None:
        self._cache = None

    @contextmanager
    def _s(self) -> Iterator:
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _audit(s, entity_type: str, entity_id: int, action: str, summary: str) -> None:
        s.add(AuditRow(timestamp=_now(), entity_type=entity_type,
                       entity_id=entity_id, action=action, summary=summary))

    # ── Categories ──────────────────────────────────────────────────────────

    @_request_cached
    def list_categories(self, active_only: bool = False) -> list[ExpenseCategory]:
        with self._s() as s:
            rows = s.scalars(select(CategoryRow)).all()
            cats = [_cat(r) for r in rows]
        if active_only:
            cats = [c for c in cats if c.is_active]
        return sorted(cats, key=lambda c: (c.scope.value, c.name))

    def get_category(self, cat_id: int) -> Optional[ExpenseCategory]:
        with self._s() as s:
            r = s.get(CategoryRow, cat_id)
            return _cat(r) if r else None

    def create_category(self, name: str, scope: str,
                        monthly_budget: float = 0.0) -> ExpenseCategory:
        with self._s() as s:
            r = CategoryRow(name=name, scope=scope, is_active=True,
                            created_at=_now(), monthly_budget=monthly_budget)
            s.add(r); s.flush()
            return _cat(r)

    def update_category(self, cat_id: int, name: str, is_active: bool,
                        monthly_budget: Optional[float] = None) -> Optional[ExpenseCategory]:
        with self._s() as s:
            r = s.get(CategoryRow, cat_id)
            if r is None:
                return None
            r.name = name
            r.is_active = is_active
            if monthly_budget is not None:
                r.monthly_budget = monthly_budget
            s.flush()
            return _cat(r)

    def seed_if_empty(self) -> None:
        self.seed_settings_if_empty()
        with self._s() as s:
            if s.scalars(select(CategoryRow.id)).first() is not None:
                return
            now = _now()
            event_cats = ["Photographer", "Editor", "Videographer", "Album", "Food",
                          "Travel", "Hard Disk", "Reels Creator", "Miscellaneous", "Commission"]
            company_cats = ["Marketing", "Travel", "Equipment", "Software", "Office"]
            personal_cats = ["Founder expenses"]
            for name in event_cats:
                s.add(CategoryRow(name=name, scope="event", is_active=True, created_at=now))
            for name in company_cats:
                s.add(CategoryRow(name=name, scope="company", is_active=True, created_at=now))
            for name in personal_cats:
                s.add(CategoryRow(name=name, scope="personal", is_active=True, created_at=now))

    # ── Events ────────────────────────────────────────────────────────────────

    @_request_cached
    def list_events(self) -> list[Event]:
        with self._s() as s:
            rows = s.scalars(select(EventRow).order_by(EventRow.id)).all()
            return [_event(r) for r in rows]

    def get_event(self, event_id: int) -> Optional[Event]:
        with self._s() as s:
            r = s.get(EventRow, event_id)
            return _event(r) if r else None

    def create_event(self, name: str, client_name: Optional[str] = None,
                     event_date: Optional[date] = None, quoted_amount: float = 0.0,
                     status: str = "active", notes: Optional[str] = None,
                     event_type: Optional[str] = None, location: Optional[str] = None,
                     referral_source: Optional[str] = None,
                     payment_due_dates: Optional[str] = None,
                     last_reminder_sent: Optional[date] = None,
                     reminder_notes: Optional[str] = None,
                     client_id: Optional[int] = None,
                     delivery_status: Optional[str] = None) -> Event:
        with self._s() as s:
            r = EventRow(
                name=name, client_name=client_name, event_date=event_date,
                quoted_amount=quoted_amount, status=status, notes=notes,
                created_at=_now(), event_type=event_type, location=location,
                referral_source=referral_source, payment_due_dates=payment_due_dates,
                last_reminder_sent=last_reminder_sent, reminder_notes=reminder_notes,
                client_id=client_id, delivery_status=delivery_status,
            )
            s.add(r); s.flush()
            self._audit(s, "event", r.id, "create", f"Created event '{name}'")
            return _event(r)

    def update_event(self, event_id: int, name: str, client_name: Optional[str] = None,
                     event_date: Optional[date] = None, quoted_amount: float = 0.0,
                     status: str = "active", notes: Optional[str] = None,
                     event_type: Optional[str] = None, location: Optional[str] = None,
                     referral_source: Optional[str] = None,
                     payment_due_dates: Optional[str] = None,
                     last_reminder_sent: Optional[date] = None,
                     reminder_notes: Optional[str] = None,
                     client_id: Optional[int] = None,
                     delivery_status: Optional[str] = None) -> Optional[Event]:
        with self._s() as s:
            r = s.get(EventRow, event_id)
            if r is None:
                return None
            # Preserve existing values when caller passes None (matches old semantics).
            r.name = name
            r.client_name = client_name
            r.event_date = event_date
            r.quoted_amount = quoted_amount
            r.status = status
            r.notes = notes
            r.event_type = event_type
            r.location = location
            r.referral_source = referral_source
            if payment_due_dates is not None:
                r.payment_due_dates = payment_due_dates
            if last_reminder_sent is not None:
                r.last_reminder_sent = last_reminder_sent
            if reminder_notes is not None:
                r.reminder_notes = reminder_notes
            if client_id is not None:
                r.client_id = client_id
            if delivery_status is not None:
                r.delivery_status = delivery_status
            s.flush()
            return _event(r)

    def set_event_reminder(self, event_id: int, reminder_date: date,
                           notes: Optional[str] = None) -> Optional[Event]:
        with self._s() as s:
            r = s.get(EventRow, event_id)
            if r is None:
                return None
            r.last_reminder_sent = reminder_date
            r.reminder_notes = notes
            s.flush()
            return _event(r)

    def set_event_payment_schedule(self, event_id: int,
                                   payment_due_dates: Optional[str]) -> Optional[Event]:
        with self._s() as s:
            r = s.get(EventRow, event_id)
            if r is None:
                return None
            r.payment_due_dates = payment_due_dates
            s.flush()
            return _event(r)

    def delete_event(self, event_id: int) -> None:
        with self._s() as s:
            r = s.get(EventRow, event_id)
            ev_name = r.name if r else str(event_id)
            for p in s.scalars(select(PaymentRow).where(PaymentRow.event_id == event_id)).all():
                s.delete(p)
            for e in s.scalars(select(ExpenseRow).where(ExpenseRow.event_id == event_id)).all():
                s.delete(e)
            if r is not None:
                s.delete(r)
            self._audit(s, "event", event_id, "delete", f"Deleted event '{ev_name}'")

    # ── Clients ─────────────────────────────────────────────────────────────

    @_request_cached
    def list_clients(self) -> list[Client]:
        with self._s() as s:
            return [_client(r) for r in s.scalars(select(ClientRow).order_by(ClientRow.id)).all()]

    def get_client(self, client_id: int) -> Optional[Client]:
        with self._s() as s:
            r = s.get(ClientRow, client_id)
            return _client(r) if r else None

    def create_client(self, name: str, phone: Optional[str] = None,
                      email: Optional[str] = None, address: Optional[str] = None,
                      notes: Optional[str] = None) -> Client:
        with self._s() as s:
            r = ClientRow(name=name, phone=phone, email=email, address=address,
                          notes=notes, created_at=_now())
            s.add(r); s.flush()
            self._audit(s, "client", r.id, "create", f"Created client '{name}'")
            return _client(r)

    def update_client(self, client_id: int, name: str, phone: Optional[str] = None,
                      email: Optional[str] = None, address: Optional[str] = None,
                      notes: Optional[str] = None) -> Optional[Client]:
        with self._s() as s:
            r = s.get(ClientRow, client_id)
            if r is None:
                return None
            r.name = name; r.phone = phone; r.email = email
            r.address = address; r.notes = notes
            s.flush()
            return _client(r)

    def delete_client(self, client_id: int) -> None:
        with self._s() as s:
            r = s.get(ClientRow, client_id)
            name = r.name if r else str(client_id)
            if r is not None:
                s.delete(r)
            self._audit(s, "client", client_id, "delete", f"Deleted client '{name}'")

    # ── Payments ──────────────────────────────────────────────────────────────

    @_request_cached
    def list_payments(self, event_id: Optional[int] = None) -> list[EventPayment]:
        with self._s() as s:
            stmt = select(PaymentRow).order_by(PaymentRow.id)
            if event_id is not None:
                stmt = stmt.where(PaymentRow.event_id == event_id)
            return [_payment(r) for r in s.scalars(stmt).all()]

    def create_payment(self, event_id: int, amount: float,
                       payment_date: date, notes: Optional[str] = None) -> EventPayment:
        with self._s() as s:
            r = PaymentRow(event_id=event_id, amount=amount,
                           payment_date=payment_date, notes=notes, created_at=_now())
            s.add(r); s.flush()
            return _payment(r)

    def delete_payment(self, payment_id: int) -> None:
        with self._s() as s:
            r = s.get(PaymentRow, payment_id)
            if r is not None:
                s.delete(r)

    # ── Expenses ──────────────────────────────────────────────────────────────

    @_request_cached
    def list_expenses(self, event_id: Optional[int] = None, category_id: Optional[int] = None,
                      scope: Optional[str] = None, status: Optional[str] = None,
                      date_from: Optional[date] = None, date_to: Optional[date] = None,
                      include_estimates: bool = False) -> list[Expense]:
        with self._s() as s:
            stmt = select(ExpenseRow)
            if event_id is not None:
                stmt = stmt.where(ExpenseRow.event_id == event_id)
            if category_id is not None:
                stmt = stmt.where(ExpenseRow.category_id == category_id)
            if scope is not None:
                stmt = stmt.where(ExpenseRow.scope == scope)
            if status is not None:
                stmt = stmt.where(ExpenseRow.payment_status == status)
            elif not include_estimates:
                # Estimates are planning-only — excluded from all actual-money queries
                # by default (payables, profit, KPIs). Opt in with include_estimates=True.
                stmt = stmt.where(ExpenseRow.payment_status != "estimated")
            if date_from is not None:
                stmt = stmt.where(ExpenseRow.date >= date_from)
            if date_to is not None:
                stmt = stmt.where(ExpenseRow.date <= date_to)
            expenses = [_expense(r) for r in s.scalars(stmt).all()]
        expenses.sort(key=lambda e: e.date, reverse=True)
        return expenses

    def get_expense(self, expense_id: int) -> Optional[Expense]:
        with self._s() as s:
            r = s.get(ExpenseRow, expense_id)
            return _expense(r) if r else None

    def create_expense(self, date_: date, category_id: int, scope: str,
                       payment_status: str, amount: float, event_id: Optional[int] = None,
                       paid_amount: float = 0.0, paid_to: Optional[str] = None,
                       notes: Optional[str] = None, payee_id: Optional[int] = None,
                       is_recurring: bool = False,
                       recurring_day: Optional[int] = None,
                       payment_type: Optional[str] = None) -> Expense:
        with self._s() as s:
            r = ExpenseRow(
                date=date_, event_id=event_id, category_id=category_id, scope=scope,
                payment_status=payment_status, amount=amount, paid_amount=paid_amount,
                paid_to=paid_to, notes=notes, created_at=_now(), payee_id=payee_id,
                is_recurring=is_recurring, recurring_day=recurring_day,
                payment_type=payment_type or None,
            )
            s.add(r); s.flush()
            self._audit(s, "expense", r.id, "create",
                        f"Created {'recurring ' if is_recurring else ''}expense ₹{amount:,.0f} [{scope}]")
            return _expense(r)

    def update_expense(self, expense_id: int, date_: date, category_id: int, scope: str,
                       payment_status: str, amount: float, event_id: Optional[int] = None,
                       paid_amount: float = 0.0, paid_to: Optional[str] = None,
                       notes: Optional[str] = None, payee_id: Optional[int] = None,
                       is_recurring: Optional[bool] = None,
                       recurring_day: Optional[int] = None,
                       payment_type: Optional[str] = None) -> Optional[Expense]:
        with self._s() as s:
            r = s.get(ExpenseRow, expense_id)
            if r is None:
                return None
            r.date = date_; r.event_id = event_id; r.category_id = category_id
            r.scope = scope; r.payment_status = payment_status
            r.amount = amount; r.paid_amount = paid_amount
            r.paid_to = paid_to; r.notes = notes
            if payee_id is not None:
                r.payee_id = payee_id
            if is_recurring is not None:
                r.is_recurring = is_recurring
            if recurring_day is not None:
                r.recurring_day = recurring_day
            if payment_type is not None:
                r.payment_type = payment_type or None
            s.flush()
            return _expense(r)

    def delete_expense(self, expense_id: int) -> None:
        with self._s() as s:
            r = s.get(ExpenseRow, expense_id)
            if r is not None:
                s.delete(r)
            self._audit(s, "expense", expense_id, "delete", f"Deleted expense #{expense_id}")

    # ── Payees ────────────────────────────────────────────────────────────────

    def list_payees(self) -> list[Payee]:
        with self._s() as s:
            return [_payee(r) for r in s.scalars(select(PayeeRow).order_by(PayeeRow.id)).all()]

    def get_payee(self, payee_id: int) -> Optional[Payee]:
        with self._s() as s:
            r = s.get(PayeeRow, payee_id)
            return _payee(r) if r else None

    def create_payee(self, name: str, payee_type: str = "freelancer",
                     phone: Optional[str] = None, email: Optional[str] = None,
                     notes: Optional[str] = None) -> Payee:
        with self._s() as s:
            r = PayeeRow(name=name, payee_type=payee_type, phone=phone,
                         email=email, notes=notes, created_at=_now())
            s.add(r); s.flush()
            self._audit(s, "payee", r.id, "create", f"Created {payee_type} '{name}'")
            return _payee(r)

    def update_payee(self, payee_id: int, name: str, payee_type: str = "freelancer",
                     phone: Optional[str] = None, email: Optional[str] = None,
                     notes: Optional[str] = None) -> Optional[Payee]:
        with self._s() as s:
            r = s.get(PayeeRow, payee_id)
            if r is None:
                return None
            r.name = name; r.payee_type = payee_type; r.phone = phone
            r.email = email; r.notes = notes
            s.flush()
            return _payee(r)

    def delete_payee(self, payee_id: int) -> None:
        with self._s() as s:
            r = s.get(PayeeRow, payee_id)
            name = r.name if r else str(payee_id)
            if r is not None:
                s.delete(r)
            self._audit(s, "payee", payee_id, "delete", f"Deleted payee '{name}'")

    # ── Leads ─────────────────────────────────────────────────────────────────

    @_request_cached
    def list_leads(self, status: Optional[str] = None) -> list[Lead]:
        with self._s() as s:
            stmt = select(LeadRow)
            if status:
                stmt = stmt.where(LeadRow.status == status)
            leads = [_lead(r) for r in s.scalars(stmt).all()]
        leads.sort(key=lambda l: l.created_at, reverse=True)
        return leads

    def get_lead(self, lead_id: int) -> Optional[Lead]:
        with self._s() as s:
            r = s.get(LeadRow, lead_id)
            return _lead(r) if r else None

    def get_lead_by_meta_id(self, meta_lead_id: str) -> Optional[Lead]:
        with self._s() as s:
            r = s.scalars(select(LeadRow).where(LeadRow.meta_lead_id == meta_lead_id)).first()
            return _lead(r) if r else None

    def create_lead(self, client_name: str, contact: str = "",
                    event_type: str = "", tentative_date: Optional[date] = None,
                    source: str = "", status: str = "new",
                    quoted_amount: float = 0.0, notes: str = "",
                    client_id: Optional[int] = None,
                    num_events: int = 0, revised_quote: float = 0.0,
                    follow_ups: str = "", rejection_reason: str = "",
                    meta_campaign: bool = False, referral_name: str = "",
                    followup_status: str = "pending",
                    followup_date: Optional[date] = None,
                    meta_lead_id: Optional[str] = None,
                    meta_campaign_name: Optional[str] = None,
                    meta_form_id: Optional[str] = None) -> Lead:
        with self._s() as s:
            r = LeadRow(
                client_name=client_name, contact=contact, event_type=event_type,
                tentative_date=tentative_date, source=source, status=status,
                quoted_amount=quoted_amount, notes=notes, created_at=_now(),
                client_id=client_id, num_events=num_events, revised_quote=revised_quote,
                follow_ups=follow_ups, rejection_reason=rejection_reason,
                meta_campaign=meta_campaign, referral_name=referral_name,
                followup_status=followup_status, followup_date=followup_date,
                meta_lead_id=meta_lead_id, meta_campaign_name=meta_campaign_name,
                meta_form_id=meta_form_id,
            )
            # A lost lead has no pending follow-up.
            if status == "lost":
                r.followup_status = "done"
            s.add(r); s.flush()
            self._audit(s, "lead", r.id, "create", f"Created lead for '{client_name}'")
            return _lead(r)

    def update_lead(self, lead_id: int, client_name: str, contact: str = "",
                    event_type: str = "", tentative_date: Optional[date] = None,
                    source: str = "", status: str = "new",
                    quoted_amount: float = 0.0, notes: str = "",
                    client_id: Optional[int] = None,
                    num_events: int = 0, revised_quote: float = 0.0,
                    follow_ups: str = "", rejection_reason: str = "",
                    meta_campaign: bool = False, referral_name: str = "",
                    followup_status: str = "pending",
                    followup_date: Optional[date] = None) -> Optional[Lead]:
        with self._s() as s:
            r = s.get(LeadRow, lead_id)
            if r is None:
                return None
            r.client_name = client_name; r.contact = contact
            r.event_type = event_type; r.tentative_date = tentative_date
            r.source = source; r.status = status
            r.quoted_amount = quoted_amount; r.notes = notes; r.client_id = client_id
            r.num_events = num_events; r.revised_quote = revised_quote
            r.follow_ups = follow_ups; r.rejection_reason = rejection_reason
            r.meta_campaign = meta_campaign; r.referral_name = referral_name
            # A lost lead has no pending follow-up — force it done (#1).
            r.followup_status = "done" if status == "lost" else followup_status
            r.followup_date = followup_date
            s.flush()
            return _lead(r)

    def delete_lead(self, lead_id: int) -> None:
        with self._s() as s:
            r = s.get(LeadRow, lead_id)
            name = r.client_name if r else str(lead_id)
            if r is not None:
                s.delete(r)
            self._audit(s, "lead", lead_id, "delete", f"Deleted lead '{name}'")

    # ── Enrichment ────────────────────────────────────────────────────────────

    def enrich_event(self, event: Event) -> Event:
        cats = {c.id: c for c in self.list_categories()}
        event.payments = self.list_payments(event_id=event.id)
        event.expenses = self.list_expenses(event_id=event.id)
        for exp in event.expenses:
            exp.category = cats.get(exp.category_id)
        return event

    @_request_cached
    def list_events_enriched(self) -> list[Event]:
        events = self.list_events()
        all_pays = self.list_payments()
        all_exps = self.list_expenses()
        cats = {c.id: c for c in self.list_categories()}

        pays_by_event: dict[int, list[EventPayment]] = {}
        for p in all_pays:
            pays_by_event.setdefault(p.event_id, []).append(p)

        exps_by_event: dict[int, list[Expense]] = {}
        for e in all_exps:
            e.category = cats.get(e.category_id)
            if e.event_id:
                exps_by_event.setdefault(e.event_id, []).append(e)

        for ev in events:
            ev.payments = pays_by_event.get(ev.id, [])
            ev.expenses = exps_by_event.get(ev.id, [])
        return events

    # ── Settings ──────────────────────────────────────────────────────────────

    _SETTING_DEFAULTS: dict[str, str] = {
        "studio_name":           "Life in Frame",
        "studio_sub":            "Studio Finance",
        "currency_symbol":       "₹",
        "ar_grace_days":         "0",
        "reminder_cadence_days": "7",
        # Follow-up reminder emails (lead follow-ups feature)
        "followup_recipients":   "",     # comma-separated emails
        "followup_enabled":      "on",   # "on"/"off"
        # Google Sheet lead intake high-water mark (newest imported Timestamp)
        "leads_intake_cursor":   "",
    }

    def get_settings_dict(self) -> dict[str, str]:
        with self._s() as s:
            stored = {r.key: (r.value or "") for r in s.scalars(select(SettingRow)).all()}
        result = dict(self._SETTING_DEFAULTS)
        result.update(stored)
        return result

    def set_settings(self, settings_dict: dict[str, str]) -> None:
        with self._s() as s:
            existing = {r.key: r for r in s.scalars(select(SettingRow)).all()}
            now = _now()
            for key, value in settings_dict.items():
                if key in existing:
                    existing[key].value = str(value)
                    existing[key].updated_at = now
                else:
                    s.add(SettingRow(key=key, value=str(value), updated_at=now))

    def seed_settings_if_empty(self) -> None:
        with self._s() as s:
            if s.scalars(select(SettingRow.id)).first() is not None:
                return
            now = _now()
            for key, value in self._SETTING_DEFAULTS.items():
                s.add(SettingRow(key=key, value=str(value), updated_at=now))

    # ── Audit log ───────────────────────────────────────────────────────────

    def log_audit(self, entity_type: str, entity_id: int,
                  action: str, summary: str) -> None:
        """Best-effort audit append (own transaction); never raises."""
        try:
            with self._s() as s:
                self._audit(s, entity_type, entity_id, action, summary)
        except Exception as exc:
            log.warning("Audit log write failed: %s", exc)

    def list_audit(self, limit: int = 200,
                   entity_type: Optional[str] = None) -> list[AuditEntry]:
        with self._s() as s:
            stmt = select(AuditRow)
            if entity_type:
                stmt = stmt.where(AuditRow.entity_type == entity_type)
            entries = [_audit_entry(r) for r in s.scalars(stmt).all()]
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    # ── Recurring expenses ────────────────────────────────────────────────────

    def generate_recurring_expenses(self, target_year: int,
                                    target_month: int) -> list[Expense]:
        from calendar import monthrange
        templates_list = [e for e in self.list_expenses() if e.is_recurring]
        if not templates_list:
            return []
        existing = [
            e for e in self.list_expenses()
            if not e.is_recurring
            and e.date.year == target_year
            and e.date.month == target_month
        ]
        _, month_days = monthrange(target_year, target_month)
        created: list[Expense] = []
        for tmpl in templates_list:
            day = min(tmpl.recurring_day or 1, month_days)
            target_date = date(target_year, target_month, day)
            if any(
                e.category_id == tmpl.category_id
                and e.scope == tmpl.scope
                and e.event_id == tmpl.event_id
                and e.date == target_date
                for e in existing
            ):
                continue
            new_exp = self.create_expense(
                date_=target_date,
                category_id=tmpl.category_id,
                scope=tmpl.scope.value,
                payment_status="pending",
                amount=tmpl.amount,
                event_id=tmpl.event_id,
                paid_to=tmpl.paid_to,
                notes=(f"[Auto-generated] {tmpl.notes or ''}").strip(),
                payee_id=tmpl.payee_id,
                is_recurring=False,
            )
            created.append(new_exp)
        return created

    # ── Meta Ads metrics ──────────────────────────────────────────────────────

    def list_meta_metrics(self) -> list[MetaMetric]:
        with self._s() as s:
            rows = s.scalars(select(MetaMetricRow).order_by(MetaMetricRow.date)).all()
            return [_metric(r) for r in rows]

    def replace_meta_metrics(self, metrics: list[dict]) -> int:
        """Replace the metrics cache with a fresh pull. Returns rows written."""
        with self._s() as s:
            for r in s.scalars(select(MetaMetricRow)).all():
                s.delete(r)
            now = _now()
            for m in metrics:
                s.add(MetaMetricRow(
                    campaign_id=str(m.get("campaign_id", "")),
                    campaign_name=m.get("campaign_name", ""),
                    date=m["date"],
                    spend=float(m.get("spend", 0) or 0),
                    impressions=int(m.get("impressions", 0) or 0),
                    reach=int(m.get("reach", 0) or 0),
                    clicks=int(m.get("clicks", 0) or 0),
                    leads=int(m.get("leads", 0) or 0),
                    cpl=float(m.get("cpl", 0) or 0),
                    currency=m.get("currency", ""),
                    fetched_at=now,
                ))
            return len(metrics)


# ─── FastAPI dependency ──────────────────────────────────────────────────────

_db_instance: Optional[Database] = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance
