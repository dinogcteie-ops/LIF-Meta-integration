"""Delivery dashboard — post-production phase tracker.

GET /delivery shows all active projects with milestone progress, overdue
flags, and a summary strip.
"""
from datetime import date

from fastapi import APIRouter, Depends, Request

from app.database import get_db, SheetDB
from app.enums import EventStatus, EventType
from app.rbac import require
from app.services.delivery import all_phase_names, build_delivery_card
from app.services.reports import event_profits
from app.templating import templates

router = APIRouter(dependencies=[Depends(require("finance.view"))])


@router.get("/delivery")
def delivery_dashboard(
    request: Request,
    phase: str = "",
    event_type: str = "",
    db: SheetDB = Depends(get_db),
):
    today = date.today()

    # Build profit map for pending_from_client amounts
    profits = {ep.event.id: ep for ep in event_profits(db)}

    # Active/booked events only (those still in production)
    active_statuses = {EventStatus.active, EventStatus.booked}
    cards = []

    for ep in profits.values():
        ev = ep.event
        if ev.status not in active_statuses:
            continue

        milestones = db.list_milestones(event_id=ev.id)

        card = build_delivery_card(
            event_id=ev.id,
            event_name=ev.name,
            client_name=ev.client_name,
            event_date=ev.event_date,
            event_type=ev.event_type,
            delivery_status=ev.delivery_status,
            milestones=milestones,
            pending_from_client=ep.pending_from_client,
            today=today,
        )

        # Apply filters
        if phase and card.current_phase != phase:
            continue
        if event_type and (ev.event_type or "") != event_type:
            continue

        cards.append(card)

    # Sort: most overdue first, then by event date
    cards.sort(key=lambda c: (-len(c.overdue), c.event_date or date.min))

    # Summary strip
    total_active = len(cards)
    overdue_count = sum(len(c.overdue) for c in cards)
    delivered_pending = sum(
        1 for ep in profits.values()
        if ep.event.delivery_status == "delivered" and ep.pending_from_client > 0
    )

    return templates.TemplateResponse(
        request,
        "delivery/index.html",
        {
            "cards": cards,
            "today": today,
            "total_active": total_active,
            "overdue_count": overdue_count,
            "delivered_pending": delivered_pending,
            "filters": {"phase": phase, "event_type": event_type},
            "phase_options": all_phase_names(),
            "event_types": [e.value for e in EventType],
        },
    )
