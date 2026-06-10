"""Post-production workshop — Phase 4."""
from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import DeliveryStatus, EventStatus
from app.rbac import require
from app.templating import templates

router = APIRouter(dependencies=[Depends(require("finance.view"))])

_DELIVERY_STAGES = list(DeliveryStatus)
_STAGE_ORDER = [s.value for s in _DELIVERY_STAGES]


@router.get("/workshop")
def workshop(request: Request, db: SheetDB = Depends(get_db)):
    """All completed events that haven't been marked 'delivered' yet."""
    events = [
        ev for ev in db.list_events()
        if ev.status == EventStatus.completed
        and ev.delivery_status != DeliveryStatus.delivered.value
    ]
    # Enrich with payments for each event (to show pending balance)
    pays_map = {}
    for p in db.list_payments():
        pays_map.setdefault(p.event_id, []).append(p)
    for ev in events:
        ev.payments = pays_map.get(ev.id, [])

    # Sort: no delivery_status first (brand-new), then by stage order, then by event_date
    def sort_key(ev):
        stage_idx = _STAGE_ORDER.index(ev.delivery_status) if ev.delivery_status in _STAGE_ORDER else -1
        return (stage_idx, ev.event_date or "9999-99-99")

    events.sort(key=sort_key)

    return templates.TemplateResponse(
        request, "workshop.html", {
            "events":   events,
            "stages":   _DELIVERY_STAGES,
            "today":    date_cls.today(),
        }
    )


@router.post("/workshop/{event_id}/advance")
def advance_stage(
    event_id: int,
    delivery_status: str = Form(...),
    db: SheetDB = Depends(get_db),
):
    """Set a new delivery stage for an event from the workshop board."""
    ev = db.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404)
    db.update_event(
        event_id,
        name=ev.name, client_name=ev.client_name, event_date=ev.event_date,
        quoted_amount=ev.quoted_amount, status=ev.status.value,
        notes=ev.notes, event_type=ev.event_type, location=ev.location,
        referral_source=ev.referral_source,
        delivery_status=delivery_status or None,
    )
    return RedirectResponse(url="/workshop", status_code=303)
