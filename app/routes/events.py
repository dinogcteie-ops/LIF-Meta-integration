import json
import re
from datetime import date as date_cls
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import EventStatus, EventType, LeadSource
from app.services.reports import event_profit, event_profits
from app.rbac import require
from app.templating import templates
from app.validators import parse_amount, parse_date_safe, parse_enum


def _parse_schedule(raw: str) -> Optional[str]:
    """Parse user-entered schedule text into normalized JSON string.

    Accepted line formats:
      YYYY-MM-DD : 50000 : Booking
      YYYY-MM-DD : 50000
      YYYY-MM-DD, 50000, Booking
    Blank lines and malformed lines are skipped silently.
    Returns JSON string or None if no valid installments parsed.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Allow either : or , as the separator
        parts = [p.strip() for p in re.split(r"[,:]", line) if p.strip()]
        if len(parts) < 2:
            continue
        try:
            d = date_cls.fromisoformat(parts[0])
        except ValueError:
            continue
        try:
            amt = float(parts[1].replace(",", ""))
        except ValueError:
            continue
        label = parts[2] if len(parts) >= 3 else ""
        items.append({"date": d.isoformat(), "amount": amt, "label": label})
    if not items:
        return None
    items.sort(key=lambda x: x["date"])
    return json.dumps(items, ensure_ascii=False)


# ─── Standard 3-stage payment plan ────────────────────────────────────────────
# The studio's default schedule. The detail page renders these as three fixed
# rows; anything else the user typed survives in the "additional installments"
# freeform box.
SCHEDULE_ADVANCE  = "Booking advance"
SCHEDULE_EVENTDAY = "Event day"
SCHEDULE_DELIVERY = "On delivery"

# Map a stored label (lower-cased) back onto one of the three standard rows.
_STD_LABEL_KEYS = {
    "booking advance": "advance", "advance": "advance",
    "event day": "eventday", "event": "eventday",
    "on delivery": "delivery", "delivery": "delivery",
}
_STD_LABELS = {"advance": SCHEDULE_ADVANCE,
               "eventday": SCHEDULE_EVENTDAY,
               "delivery": SCHEDULE_DELIVERY}


def _amount_str(amt) -> str:
    """Render a JSON amount back into an input value (int when whole)."""
    try:
        a = float(amt)
    except (TypeError, ValueError):
        return ""
    return f"{int(a)}" if a == int(a) else f"{a}"


def _split_schedule(schedule_json: Optional[str]) -> tuple[dict, str]:
    """Split a stored schedule into the 3 standard rows + freeform extras.

    Returns (std, extra_text) where std maps advance/eventday/delivery to
    {"date", "amount"} strings ready for the form inputs.
    """
    std = {k: {"date": "", "amount": ""} for k in _STD_LABELS}
    if not schedule_json:
        return std, ""
    try:
        items = json.loads(schedule_json)
    except (ValueError, TypeError):
        return std, ""
    extra_lines = []
    for it in items:
        label = (it.get("label") or "").strip()
        key = _STD_LABEL_KEYS.get(label.lower())
        amt = _amount_str(it.get("amount", 0))
        if key and not std[key]["date"] and not std[key]["amount"]:
            std[key] = {"date": it.get("date", ""), "amount": amt}
        elif label:
            extra_lines.append(f"{it.get('date','')} : {amt} : {label}")
        else:
            extra_lines.append(f"{it.get('date','')} : {amt}")
    return std, "\n".join(extra_lines)


def _compose_schedule(rows: list[tuple[str, str, str]],
                      extra_text: str) -> Optional[str]:
    """Build schedule JSON from the 3 standard (date, amount, label) rows plus
    any freeform extra installments. A row needs both a date and an amount to
    count. Returns JSON string or None when nothing valid was supplied."""
    items = []
    for d, a, label in rows:
        d = (d or "").strip()
        a = (a or "").strip()
        if not d or not a:
            continue
        try:
            dt = date_cls.fromisoformat(d)
        except ValueError:
            continue
        try:
            amt = float(a.replace(",", ""))
        except ValueError:
            continue
        items.append({"date": dt.isoformat(), "amount": amt, "label": label})
    extra_json = _parse_schedule(extra_text)
    if extra_json:
        try:
            items.extend(json.loads(extra_json))
        except (ValueError, TypeError):
            pass
    if not items:
        return None
    items.sort(key=lambda x: x["date"])
    return json.dumps(items, ensure_ascii=False)


def _annotate_schedule(schedule_json: Optional[str], payments) -> list[dict]:
    """Combine schedule with cumulative-paid logic for the detail page.

    For each installment, mark it 'paid' if cumulative received by the
    installment date >= cumulative scheduled by the same date.
    """
    if not schedule_json:
        return []
    try:
        items = json.loads(schedule_json)
    except (ValueError, TypeError):
        return []
    # Sort payments by date so cumulative works
    sorted_pays = sorted(payments, key=lambda p: p.payment_date)
    annotated = []
    cumulative_scheduled = 0.0
    for it in items:
        try:
            installment_date = date_cls.fromisoformat(it["date"])
        except (KeyError, ValueError):
            continue
        amount = float(it.get("amount", 0))
        label = it.get("label", "")
        cumulative_scheduled += amount
        cumulative_received_by_date = sum(
            p.amount for p in sorted_pays if p.payment_date <= installment_date
        )
        # Past due if installment date has passed today and not paid
        is_paid = cumulative_received_by_date + 0.01 >= cumulative_scheduled
        is_overdue = (not is_paid) and (installment_date < date_cls.today())
        annotated.append({
            "date": installment_date,
            "amount": amount,
            "label": label,
            "is_paid": is_paid,
            "is_overdue": is_overdue,
            "cumulative_scheduled": round(cumulative_scheduled, 2),
            "cumulative_received": round(cumulative_received_by_date, 2),
        })
    return annotated

INDIAN_CITIES = [
    "Ahmedabad", "Bangalore", "Chennai", "Coimbatore", "Delhi",
    "Goa", "Hyderabad", "Jaipur", "Kochi", "Kolkata", "Lucknow",
    "Mumbai", "Mysore", "Nagpur", "Pune", "Surat", "Visakhapatnam",
]

router = APIRouter(dependencies=[Depends(require("finance.view"))])


@router.get("/events")
def list_events(
    request: Request,
    status: str = "",
    event_type: str = "",
    date_from: str = "",
    date_to: str = "",
    db: SheetDB = Depends(get_db),
):
    rows = event_profits(db)
    # Apply filters (QW1)
    if status:
        rows = [r for r in rows if r.event.status.value == status]
    if event_type:
        rows = [r for r in rows if (r.event.event_type or "") == event_type]
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    if df:
        rows = [r for r in rows if r.event.event_date and r.event.event_date >= df]
    if dt:
        rows = [r for r in rows if r.event.event_date and r.event.event_date <= dt]
    return templates.TemplateResponse(
        request, "events/list.html", {
            "rows": rows,
            "statuses": list(EventStatus),
            "event_types": list(EventType),
            "filters": {
                "status": status,
                "event_type": event_type,
                "date_from": date_from,
                "date_to": date_to,
            },
        }
    )


@router.get("/events/new")
def new_event_form(request: Request, db: SheetDB = Depends(get_db)):
    return templates.TemplateResponse(
        request, "events/form.html", {
            "event": None,
            "statuses": list(EventStatus),
            "event_types": list(EventType),
            "lead_sources": list(LeadSource),
            "cities": INDIAN_CITIES,
            "clients": db.list_clients(),
        },
    )


@router.post("/events")
def create_event(
    request: Request,
    name: str = Form(...),
    client_name: str = Form(""),
    client_id: str = Form(""),
    event_date: str = Form(""),
    quoted_amount: float = Form(0.0),
    status: str = Form("active"),
    notes: str = Form(""),
    event_type: str = Form(""),
    location: str = Form(""),
    referral_source: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    error = _validate_event_input(status, quoted_amount, event_date)
    if error:
        request.session["flash"] = error
        return RedirectResponse(url="/events/new", status_code=303)
    cid = int(client_id) if client_id.strip() else None
    et = event_type.strip() or None
    ev = db.create_event(
        name=name.strip(),
        client_name=client_name.strip() or None,
        client_id=cid,
        event_date=_parse_date(event_date),
        quoted_amount=quoted_amount,
        status=status,
        notes=notes.strip() or None,
        event_type=et,
        location=location.strip() or None,
        referral_source=referral_source.strip() or None,
    )
    db.seed_milestones(ev.id, et)
    return RedirectResponse(url=f"/events/{ev.id}", status_code=303)


@router.get("/events/{event_id}")
def event_detail(event_id: int, request: Request, db: SheetDB = Depends(get_db)):
    ep = event_profit(db, event_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Event not found")
    categories = db.list_categories(active_only=True)

    cat_totals: dict[str, float] = {}
    for exp in ep.event.expenses:
        raw = exp.category.name if exp.category else "Other"
        label = re.sub(r'[-\s]+\d+$', '', raw).strip()
        cat_totals[label] = round(cat_totals.get(label, 0.0) + exp.amount, 2)
    cat_totals = dict(sorted(cat_totals.items(), key=lambda x: x[1], reverse=True))

    # Estimated (planning-only) costs for this event — separate from actuals.
    estimates = db.list_expenses(event_id=event_id, status="estimated")
    cats_all = {c.id: c for c in db.list_categories()}
    for e in estimates:
        e.category = cats_all.get(e.category_id)
    estimates.sort(key=lambda e: e.amount, reverse=True)
    estimated_total = round(sum(e.amount for e in estimates), 2)
    projected_profit = round(ep.event.quoted_amount - (ep.expense + estimated_total), 2)

    # Phase 1.2: payment schedule rendering (read-only table + structured editor)
    schedule_rows = _annotate_schedule(ep.event.payment_due_dates, ep.event.payments)
    std_schedule, schedule_extra_text = _split_schedule(ep.event.payment_due_dates)

    clients = db.list_clients()
    clients_map = {c.id: c for c in clients}
    # Resolve linked client for display
    linked_client = clients_map.get(ep.event.client_id) if ep.event.client_id else None

    # Milestones for this event
    milestones = db.list_milestones(event_id=event_id)
    today = date_cls.today()

    return templates.TemplateResponse(
        request,
        "events/detail.html",
        {
            "ep": ep,
            "event": ep.event,
            "estimates": estimates,
            "estimated_total": estimated_total,
            "projected_profit": projected_profit,
            "categories": categories,
            "statuses": list(EventStatus),
            "event_types": list(EventType),
            "lead_sources": list(LeadSource),
            "cities": INDIAN_CITIES,
            "schedule_rows": schedule_rows,
            "std_schedule": std_schedule,
            "schedule_extra_text": schedule_extra_text,
            "clients": clients,
            "linked_client": linked_client,
            "milestones": milestones,
            "today": today,
            "cat_chart": {
                "labels": list(cat_totals.keys()),
                "data": list(cat_totals.values()),
            },
        },
    )


@router.post("/events/{event_id}")
def update_event(
    event_id: int,
    request: Request,
    name: str = Form(...),
    client_name: str = Form(""),
    client_id: str = Form(""),
    event_date: str = Form(""),
    quoted_amount: float = Form(0.0),
    status: str = Form("active"),
    notes: str = Form(""),
    event_type: str = Form(""),
    location: str = Form(""),
    referral_source: str = Form(""),
    adv_date: str = Form(""),
    adv_amount: str = Form(""),
    eventday_date: str = Form(""),
    eventday_amount: str = Form(""),
    delivery_date: str = Form(""),
    delivery_amount: str = Form(""),
    payment_schedule_extra: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    error = _validate_event_input(status, quoted_amount, event_date)
    if error:
        request.session["flash"] = error
        return RedirectResponse(url=f"/events/{event_id}", status_code=303)
    schedule_json = _compose_schedule(
        [
            (adv_date, adv_amount, SCHEDULE_ADVANCE),
            (eventday_date, eventday_amount, SCHEDULE_EVENTDAY),
            (delivery_date, delivery_amount, SCHEDULE_DELIVERY),
        ],
        payment_schedule_extra,
    )
    cid = int(client_id) if client_id.strip() else None
    # delivery_status is no longer set here — it is derived from milestones
    # (db.sync_delivery_status), so the manual stage control was removed.
    ev = db.update_event(
        event_id,
        name=name.strip(),
        client_name=client_name.strip() or None,
        client_id=cid,
        event_date=_parse_date(event_date),
        quoted_amount=quoted_amount,
        status=status,
        notes=notes.strip() or None,
        event_type=event_type.strip() or None,
        location=location.strip() or None,
        referral_source=referral_source.strip() or None,
        payment_due_dates=schedule_json,
    )
    if ev is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/delete")
def delete_event(event_id: int, db: SheetDB = Depends(get_db)):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404)
    db.delete_event(event_id)
    return RedirectResponse(url="/events", status_code=303)


@router.post("/events/{event_id}/payments")
def add_payment(
    event_id: int,
    request: Request,
    amount: float = Form(...),
    payment_date: str = Form(...),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404)
    _, err = parse_amount(amount, "Payment amount")
    if err:
        request.session["flash"] = err
        return RedirectResponse(url=f"/events/{event_id}", status_code=303)
    db.create_payment(
        event_id=event_id,
        amount=amount,
        payment_date=_parse_date(payment_date) or date_cls.today(),
        notes=notes.strip() or None,
    )
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/payments/{payment_id}/delete")
def delete_payment(event_id: int, payment_id: int, db: SheetDB = Depends(get_db)):
    payments = db.list_payments(event_id=event_id)
    if not any(p.id == payment_id for p in payments):
        raise HTTPException(status_code=404)
    db.delete_payment(payment_id)
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/milestones/seed")
def seed_event_milestones(event_id: int, request: Request, db: SheetDB = Depends(get_db)):
    """Populate the default phase milestones for an event that has none yet.

    For legacy events created before milestones existed (the add-form is always
    shown, but a one-click default pipeline is friendlier).
    """
    ev = db.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404)
    db.seed_milestones(event_id, ev.event_type)
    db.sync_delivery_status(event_id)
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/milestones")
def add_milestone(
    event_id: int,
    request: Request,
    phase: str = Form(...),
    due_date: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404)
    if not phase.strip():
        request.session["flash"] = "Give the milestone a phase name."
        return RedirectResponse(url=f"/events/{event_id}", status_code=303)
    milestones = db.list_milestones(event_id=event_id)
    position = len(milestones)
    db.create_milestone(
        event_id=event_id, phase=phase.strip(),
        position=position,
        due_date=_parse_date(due_date),
        notes=notes.strip(),
    )
    db.sync_delivery_status(event_id)
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/milestones/{m_id}/toggle")
def toggle_milestone(event_id: int, m_id: int, db: SheetDB = Depends(get_db)):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404)
    db.toggle_milestone(m_id)
    db.sync_delivery_status(event_id)
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/milestones/{m_id}")
def update_milestone(
    event_id: int,
    m_id: int,
    due_date: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404)
    db.update_milestone(m_id, due_date=_parse_date(due_date), notes=notes.strip())
    db.sync_delivery_status(event_id)
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/milestones/{m_id}/delete")
def delete_milestone(event_id: int, m_id: int, db: SheetDB = Depends(get_db)):
    if db.get_event(event_id) is None:
        raise HTTPException(status_code=404)
    db.delete_milestone(m_id)
    db.sync_delivery_status(event_id)
    return RedirectResponse(url=f"/events/{event_id}", status_code=303)


def _parse_date(value: str) -> Optional[date_cls]:
    # Safe parse: malformed/absurd dates become None instead of a 500.
    return parse_date_safe(value, "Event date")[0]


def _validate_event_input(status: str, quoted_amount: float,
                          event_date: str) -> Optional[str]:
    """Returns a flash message on the first problem, None when OK."""
    _, err = parse_enum(EventStatus, status, "event status")
    if err:
        return err
    _, err = parse_amount(quoted_amount, "Quoted amount")
    if err:
        return err
    _, err = parse_date_safe(event_date, "Event date")
    if err:
        return err
    return None
