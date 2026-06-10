"""Auto-post recurring expenses (rent, salaries, subscriptions…).

An expense flagged ``is_recurring`` acts as the *template*: each month, once its
``recurring_day`` (or the template's own day-of-month) arrives, the daily job
posts a copy into the current month so nobody has to re-enter rent by hand.

Posted copies:
  * are **pending**, not paid — money shouldn't be marked as moved until the
    owner confirms it (they surface in Payables for one-click review);
  * are **not** themselves recurring (no template multiplication);
  * carry a ``[auto-recurring #<template_id>]`` notes marker, which doubles as
    the idempotency key — re-running the job in the same month is a no-op.

The template's own month never gets a copy (the template row *is* that month's
expense). Day overflow clamps to the month's last day (rent on the 31st posts on
Feb 28).
"""
from __future__ import annotations

import calendar
from datetime import date

from app.database import SheetDB

_MARKER = "[auto-recurring #{template_id}]"


def _marker(template_id: int) -> str:
    return _MARKER.format(template_id=template_id)


def _effective_day(day: int, year: int, month: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])


def post_due_recurring(db: SheetDB, today: date | None = None,
                       dry_run: bool = False) -> dict:
    """Post copies of recurring templates that are due this month. Idempotent."""
    today = today or date.today()
    all_expenses = db.list_expenses(include_estimates=True)
    templates = [e for e in all_expenses if e.is_recurring]

    this_month = [e for e in all_expenses
                  if e.date.year == today.year and e.date.month == today.month]

    posted, skipped = [], 0
    for tpl in templates:
        due_day = _effective_day(tpl.recurring_day or tpl.date.day,
                                 today.year, today.month)
        if today.day < due_day:
            skipped += 1            # not due yet this month
            continue
        if (tpl.date.year, tpl.date.month) == (today.year, today.month):
            skipped += 1            # the template row IS this month's expense
            continue
        mark = _marker(tpl.id)
        if any(mark in (e.notes or "") for e in this_month):
            skipped += 1            # already posted this month
            continue

        post_date = date(today.year, today.month, due_day)
        if not dry_run:
            db.create_expense(
                date_=post_date,
                category_id=tpl.category_id,
                scope=tpl.scope.value,
                payment_status="pending",
                amount=tpl.amount,
                event_id=None,
                paid_amount=0.0,
                paid_to=tpl.paid_to,
                notes=f"{mark} {tpl.notes or ''}".strip(),
                payee_id=tpl.payee_id,
                payment_type=tpl.payment_type,
            )
        posted.append({"template_id": tpl.id, "amount": tpl.amount,
                       "date": post_date.isoformat(), "paid_to": tpl.paid_to or ""})

    return {"posted": len(posted), "skipped": skipped,
            "details": posted, "dry_run": dry_run}
