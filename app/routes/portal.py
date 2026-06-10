"""Client Portal & Inquiry Form — Sprint 8.

Public-facing routes that don't require login:
  /portal/<token>       — Client can view their event status & payments
  /inquiry              — Lead capture form (embeddable)
"""
import hashlib
import hmac
import time
from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import client_ip
from app.config import get_settings
from app.database import get_db, SheetDB
from app.enums import DeliveryStatus, EventStatus
from app.services.reports import event_profit
from app.templating import templates

router = APIRouter()
_settings = get_settings()

# ─── Inquiry spam protection ─────────────────────────────────────────────────
# The inquiry form is public, so bots could flood the leads table. Two cheap,
# free defences: a honeypot field (hidden input real users never fill) and a
# per-IP submission cap. Both fail "open" with a fake success page — giving
# spammers no signal about what worked.

_INQUIRY_MAX_PER_WINDOW = 5
_INQUIRY_WINDOW_SECONDS = 3600
_inquiry_hits: dict[str, list] = {}   # ip -> [timestamps]


def _inquiry_allowed(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    hits = [t for t in _inquiry_hits.get(ip, []) if now - t < _INQUIRY_WINDOW_SECONDS]
    if len(hits) >= _INQUIRY_MAX_PER_WINDOW:
        _inquiry_hits[ip] = hits
        return False
    hits.append(now)
    _inquiry_hits[ip] = hits
    if len(_inquiry_hits) > 1000:   # opportunistic pruning
        for k in [k for k, v in _inquiry_hits.items()
                  if not v or now - v[-1] > _INQUIRY_WINDOW_SECONDS]:
            _inquiry_hits.pop(k, None)
    return True


def _reset_inquiry_throttle() -> None:
    """Test hook — wipe throttle state."""
    _inquiry_hits.clear()


def _generate_token(event_id: int) -> str:
    """Generate a stable, non-guessable token for a client portal link."""
    secret = (_settings.secret_key or "lif-portal").encode()
    msg = f"portal-event-{event_id}".encode()
    return hmac.HMAC(secret, msg, hashlib.sha256).hexdigest()[:16]


def generate_portal_url(event_id: int) -> str:
    """Generate the full portal URL for an event."""
    token = _generate_token(event_id)
    return f"/portal/{event_id}/{token}"


def _verify_token(event_id: int, token: str) -> bool:
    """Verify that a portal token is valid for the given event."""
    expected = _generate_token(event_id)
    return hmac.compare_digest(token, expected)


# ─── Client Portal ───────────────────────────────────────────────────────────

@router.get("/portal/{event_id}/{token}")
def client_portal(event_id: int, token: str, request: Request, db: SheetDB = Depends(get_db)):
    """Public client portal — view event status, payments, and delivery progress."""
    if not _verify_token(event_id, token):
        raise HTTPException(status_code=404, detail="Invalid portal link")

    ep = event_profit(db, event_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Event not found")

    event = ep.event
    payments = event.payments
    total_paid = sum(p.amount for p in payments)
    total_pending = max(0, event.quoted_amount - total_paid)
    progress_pct = (total_paid / event.quoted_amount * 100) if event.quoted_amount > 0 else 0

    # Delivery timeline
    delivery_stages = [
        {"key": "booked", "label": "Booked", "icon": "bi-calendar-check"},
        {"key": "shooting_done", "label": "Shot Complete", "icon": "bi-camera"},
        {"key": "editing", "label": "Editing", "icon": "bi-pencil-square"},
        {"key": "review", "label": "Review", "icon": "bi-eye"},
        {"key": "delivered", "label": "Delivered", "icon": "bi-check-circle"},
    ]

    # Determine current stage index
    current_stage = 0
    if event.status == EventStatus.completed or event.delivery_status:
        if event.delivery_status == "delivered":
            current_stage = 4
        elif event.delivery_status == "review":
            current_stage = 3
        elif event.delivery_status == "editing":
            current_stage = 2
        elif event.delivery_status == "shooting_done":
            current_stage = 1
        else:
            current_stage = 1  # completed but no delivery status
    elif event.status == EventStatus.active:
        current_stage = 0

    return templates.TemplateResponse(
        request,
        "portal/view.html",
        {
            "event": event,
            "ep": ep,
            "payments": payments,
            "total_paid": total_paid,
            "total_pending": total_pending,
            "progress_pct": round(progress_pct, 1),
            "delivery_stages": delivery_stages,
            "current_stage": current_stage,
            "token": token,
        },
    )


# ─── Inquiry / Lead Capture Form ─────────────────────────────────────────────

@router.get("/inquiry")
def inquiry_form(request: Request, db: SheetDB = Depends(get_db)):
    """Public inquiry form for lead capture."""
    studio = db.get_settings_dict()
    return templates.TemplateResponse(
        request,
        "portal/inquiry.html",
        {
            "studio_name": studio.get("studio_name", "Life in Frame"),
            "submitted": False,
        },
    )


@router.post("/inquiry")
def submit_inquiry(
    request: Request,
    client_name: str = Form(...),
    contact: str = Form(""),
    event_type: str = Form(""),
    tentative_date: str = Form(""),
    message: str = Form(""),
    source: str = Form("Website"),
    website: str = Form(""),   # honeypot — humans never see or fill this
    db: SheetDB = Depends(get_db),
):
    """Process a public inquiry submission — creates a lead."""
    # Honeypot filled or IP over the hourly cap → show the normal thank-you
    # page but create nothing (no feedback for spammers).
    if website.strip() or not _inquiry_allowed(client_ip(request)):
        studio = db.get_settings_dict()
        return templates.TemplateResponse(
            request, "portal/inquiry.html",
            {"studio_name": studio.get("studio_name", "Life in Frame"),
             "submitted": True},
        )

    tent_date = None
    if tentative_date.strip():
        try:
            tent_date = date_cls.fromisoformat(tentative_date.strip())
        except ValueError:
            pass

    db.create_lead(
        client_name=client_name.strip(),
        contact=contact.strip(),
        event_type=event_type.strip(),
        tentative_date=tent_date,
        source=source.strip() or "Website",
        status="new",
        quoted_amount=0.0,
        notes=message.strip(),
    )

    studio = db.get_settings_dict()
    return templates.TemplateResponse(
        request,
        "portal/inquiry.html",
        {
            "studio_name": studio.get("studio_name", "Life in Frame"),
            "submitted": True,
        },
    )
