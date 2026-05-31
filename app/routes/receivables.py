"""Receivables aging — clients with outstanding balances (Phase 1.1)."""
from datetime import date as date_cls
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import EventStatus
from app.services.reports import receivables_aging
from app.templating import templates

router = APIRouter()


@router.get("/receivables")
def receivables_view(
    request: Request,
    bucket: Optional[str] = None,       # 'not_due' | '0_30' | '31_60' | '60_plus'
    db: SheetDB = Depends(get_db),
):
    from app.services.reports import payables_aging
    today = date_cls.today()
    rows, totals = receivables_aging(db, today)

    # Split all rows into 3 status groups (before bucket filter)
    all_booked    = [r for r in rows if r.event.status == EventStatus.booked]
    all_ongoing   = [r for r in rows if r.event.status in (EventStatus.active, EventStatus.completed)]
    all_cancelled = [r for r in rows if r.event.status == EventStatus.cancelled]

    def _group_pending(group):
        return round(sum(r.pending for r in group), 2)

    # Bucket filter applies to booked + ongoing; cancelled always shown in full
    if bucket:
        rows_booked  = [r for r in all_booked  if r.bucket == bucket]
        rows_ongoing = [r for r in all_ongoing if r.bucket == bucket]
    else:
        rows_booked  = all_booked
        rows_ongoing = all_ongoing
    rows_cancelled = all_cancelled  # always unfiltered

    _, pay_totals = payables_aging(db, today)
    sidebar_badges = {
        "receivables_overdue": (totals.bucket_0_30_count
                                + totals.bucket_31_60_count
                                + totals.bucket_60_plus_count),
        "payables_overdue": (pay_totals.bucket_0_30_count
                             + pay_totals.bucket_31_60_count
                             + pay_totals.bucket_60_plus_count),
    }
    return templates.TemplateResponse(
        request,
        "receivables.html",
        {
            "rows_booked":       rows_booked,
            "rows_ongoing":      rows_ongoing,
            "rows_cancelled":    rows_cancelled,
            # Group totals (always the full group, regardless of bucket filter)
            "pending_booked":    _group_pending(all_booked),
            "pending_ongoing":   _group_pending(all_ongoing),
            "pending_cancelled": _group_pending(all_cancelled),
            # Total item counts per group (before filter) — for "X of Y items" display
            "count_booked":      len(all_booked),
            "count_ongoing":     len(all_ongoing),
            "count_cancelled":   len(all_cancelled),
            "all_rows":          rows,
            "totals":            totals,
            "active_bucket":     bucket,
            "today":             today,
            "sidebar_badges":    sidebar_badges,
        },
    )


@router.post("/events/{event_id}/reminders")
def log_reminder(
    event_id: int,
    request: Request,
    reminder_date: str = Form(""),
    reminder_notes: str = Form(""),
    return_to: str = Form("/receivables"),
    db: SheetDB = Depends(get_db),
):
    """Record that a payment reminder was sent (Phase 1.5)."""
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404)
    d = date_cls.today()
    if reminder_date.strip():
        try:
            d = date_cls.fromisoformat(reminder_date.strip())
        except ValueError:
            pass
    db.set_event_reminder(
        event_id,
        reminder_date=d,
        notes=reminder_notes.strip() or None,
    )
    # Honor return_to but restrict to safe in-app paths
    target = return_to if return_to.startswith("/") else "/receivables"
    return RedirectResponse(url=target, status_code=303)
