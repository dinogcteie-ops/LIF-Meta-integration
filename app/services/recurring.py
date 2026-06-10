"""Auto-post recurring expenses (rent, salaries, subscriptions…).

An expense flagged ``is_recurring`` acts as the *template*. Two entry points
share one core so the daily cron and the Settings button can never double-post:

  * ``post_due_recurring``  — daily job: post templates whose day has arrived
    in the current month (POST /jobs/recurring-expenses, Netlify cron).
  * ``generate_for_month``  — Settings button: post ALL templates for an
    explicit month, ignoring whether the day has arrived yet.

Posted copies:
  * are **pending**, not paid — money shouldn't be marked as moved until the
    owner confirms it (they surface in Payables for one-click review);
  * are **not** themselves recurring (no template multiplication);
  * carry a ``[auto-recurring #<template_id>]`` notes marker, which doubles as
    the idempotency key — re-running either entry point is a no-op.

Dedup also recognises the legacy ``[Auto-generated]`` rows that the old
Settings-button implementation created (matched by category/scope/event in the
target month), so months generated under the old scheme don't double-post.

The template's own month never gets a copy (the template row *is* that month's
expense). Day overflow clamps to the month's last day (rent on the 31st posts
on Feb 28).
"""
from __future__ import annotations

import calendar
from datetime import date

from app.database import SheetDB

_MARKER = "[auto-recurring #{template_id}]"
_LEGACY_MARKER = "[Auto-generated]"


def _marker(template_id: int) -> str:
    return _MARKER.format(template_id=template_id)


def _effective_day(day: int, year: int, month: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])


def _post_for_month(db: SheetDB, year: int, month: int,
                    due_by: date | None = None, dry_run: bool = False) -> dict:
    """Shared core. ``due_by`` set = only post templates whose day has arrived."""
    all_expenses = db.list_expenses(include_estimates=True)
    templates = [e for e in all_expenses if e.is_recurring]

    in_month = [e for e in all_expenses
                if not e.is_recurring and e.date.year == year and e.date.month == month]

    posted, skipped = [], 0
    for tpl in templates:
        day = _effective_day(tpl.recurring_day or tpl.date.day, year, month)
        if due_by is not None and due_by.day < day:
            skipped += 1            # not due yet this month
            continue
        if (tpl.date.year, tpl.date.month) == (year, month):
            skipped += 1            # the template row IS this month's expense
            continue
        mark = _marker(tpl.id)
        already = any(
            mark in (e.notes or "")
            or (_LEGACY_MARKER in (e.notes or "")
                and e.category_id == tpl.category_id
                and e.scope == tpl.scope
                and e.event_id == tpl.event_id)
            for e in in_month
        )
        if already:
            skipped += 1            # posted earlier (either marker scheme)
            continue

        post_date = date(year, month, day)
        if not dry_run:
            db.create_expense(
                date_=post_date,
                category_id=tpl.category_id,
                scope=tpl.scope.value,
                payment_status="pending",
                amount=tpl.amount,
                event_id=tpl.event_id,
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


def post_due_recurring(db: SheetDB, today: date | None = None,
                       dry_run: bool = False) -> dict:
    """Daily-cron entry: post templates due on/before today. Idempotent."""
    today = today or date.today()
    return _post_for_month(db, today.year, today.month, due_by=today, dry_run=dry_run)


def generate_for_month(db: SheetDB, year: int, month: int) -> dict:
    """Settings-button entry: post every template for an explicit month."""
    return _post_for_month(db, year, month, due_by=None, dry_run=False)
