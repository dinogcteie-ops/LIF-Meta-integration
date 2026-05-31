"""Reports service tests — seeded through the Database (storage-agnostic)."""
from datetime import date

from app.services.db import Database
from app.services.reports import (
    bank_summary, event_profit, monthly_history, project_next,
)


def _seed_event(db: Database):
    cat = db.list_categories(active_only=True)[0]
    ev = db.create_event(name="Sushmitha Wedding", quoted_amount=150_000, status="active")
    db.create_payment(event_id=ev.id, amount=50_000, payment_date=date(2026, 1, 10))
    db.create_payment(event_id=ev.id, amount=100_000, payment_date=date(2026, 2, 5))
    db.create_expense(date_=date(2026, 1, 15), category_id=cat.id, scope="event",
                      payment_status="paid", amount=20_000, paid_amount=20_000, event_id=ev.id)
    db.create_expense(date_=date(2026, 1, 20), category_id=cat.id, scope="event",
                      payment_status="pending", amount=10_000, paid_amount=0, event_id=ev.id)
    db.create_expense(date_=date(2026, 2, 1), category_id=cat.id, scope="event",
                      payment_status="paid", amount=15_000, paid_amount=15_000, event_id=ev.id)
    return ev


def test_event_profit(db):
    ev = _seed_event(db)
    ep = event_profit(db, ev.id)
    assert ep is not None
    assert ep.income == 150_000
    assert ep.expense == 45_000        # 20k + 10k + 15k
    assert ep.profit == 105_000


def test_bank_summary(db):
    _seed_event(db)
    summary = bank_summary(db)
    assert summary.total_income >= 150_000
    assert summary.balance == summary.total_income - summary.total_paid_expense


def test_monthly_history_shape(db):
    _seed_event(db)
    rows = monthly_history(db, months=6, today=date(2026, 3, 1))
    assert len(rows) == 6
    periods = [r.period for r in rows]
    assert "2026-01" in periods and "2026-02" in periods


def test_projection_shape(db):
    history = monthly_history(db, months=6, today=date(2026, 3, 1))
    proj = project_next(history, months=3)
    assert len(proj) == 3
    for r in proj:
        assert r.projected is True
        assert r.income >= 0 and r.expense >= 0


def test_paid_total_math():
    from app.domain import Expense
    from app.enums import CategoryScope, PaymentStatus
    from app.services.reports import _paid_total

    e_paid = Expense(id=1, date=date(2026, 1, 1), category_id=1, scope=CategoryScope.event,
                     payment_status=PaymentStatus.paid, amount=10_000, paid_amount=10_000)
    e_partial = Expense(id=2, date=date(2026, 1, 1), category_id=1, scope=CategoryScope.event,
                        payment_status=PaymentStatus.partial, amount=10_000, paid_amount=4_000)
    e_pending = Expense(id=3, date=date(2026, 1, 1), category_id=1, scope=CategoryScope.event,
                        payment_status=PaymentStatus.pending, amount=10_000, paid_amount=0)
    assert _paid_total(e_paid) == 10_000
    assert _paid_total(e_partial) == 4_000
    assert _paid_total(e_pending) == 0
