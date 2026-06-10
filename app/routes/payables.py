"""Payables aging — vendor/freelancer expenses still owing (Phase 1.3)."""
from datetime import date as date_cls
from typing import Optional

from fastapi import APIRouter, Depends, Request

from app.database import get_db, SheetDB
from app.enums import EventStatus
from app.services.reports import payables_aging
from app.rbac import require
from app.templating import templates

router = APIRouter(dependencies=[Depends(require("finance.view"))])


@router.get("/payables")
def payables_view(
    request: Request,
    bucket: Optional[str] = None,
    db: SheetDB = Depends(get_db),
):
    from app.services.reports import receivables_aging
    today = date_cls.today()
    rows, totals = payables_aging(db, today)

    # Enrich ALL rows with category + event objects before grouping
    cats   = {c.id: c for c in db.list_categories()}
    events = {e.id: e for e in db.list_events()}
    for r in rows:
        r.expense.category = cats.get(r.expense.category_id)
        if r.expense.event_id:
            r.expense.event = events.get(r.expense.event_id)

    # Helper: get event status from a payable row (None if no event linked)
    def _event_status(r):
        ev = getattr(r.expense, 'event', None)
        return ev.status if ev else None

    # Split into 4 groups
    all_booked    = [r for r in rows if _event_status(r) == EventStatus.booked]
    all_ongoing   = [r for r in rows if _event_status(r) in (EventStatus.active, EventStatus.completed)]
    all_cancelled = [r for r in rows if _event_status(r) == EventStatus.cancelled]
    all_overhead  = [r for r in rows if r.expense.event_id is None]

    def _group_pending(group):
        return round(sum(r.pending for r in group), 2)

    # Bucket filter applies to booked, ongoing, overhead — cancelled always shown in full
    if bucket:
        rows_booked   = [r for r in all_booked   if r.bucket == bucket]
        rows_ongoing  = [r for r in all_ongoing  if r.bucket == bucket]
        rows_overhead = [r for r in all_overhead if r.bucket == bucket]
    else:
        rows_booked   = all_booked
        rows_ongoing  = all_ongoing
        rows_overhead = all_overhead
    rows_cancelled = all_cancelled  # always unfiltered

    _, rec_totals = receivables_aging(db, today)
    sidebar_badges = {
        "receivables_overdue": (rec_totals.bucket_0_30_count
                                + rec_totals.bucket_31_60_count
                                + rec_totals.bucket_60_plus_count),
        "payables_overdue": (totals.bucket_0_30_count
                             + totals.bucket_31_60_count
                             + totals.bucket_60_plus_count),
    }
    return templates.TemplateResponse(
        request,
        "payables.html",
        {
            "rows_booked":         rows_booked,
            "rows_ongoing":        rows_ongoing,
            "rows_cancelled":      rows_cancelled,
            "rows_overhead":       rows_overhead,
            # Group totals (always the full group, regardless of bucket filter)
            "pending_booked":      _group_pending(all_booked),
            "pending_ongoing":     _group_pending(all_ongoing),
            "pending_cancelled":   _group_pending(all_cancelled),
            "pending_overhead":    _group_pending(all_overhead),
            # Total item counts per group (before filter) — for "X of Y items" display
            "count_booked":        len(all_booked),
            "count_ongoing":       len(all_ongoing),
            "count_cancelled":     len(all_cancelled),
            "count_overhead":      len(all_overhead),
            "totals":              totals,
            "active_bucket":       bucket,
            "today":               today,
            "sidebar_badges":      sidebar_badges,
        },
    )
