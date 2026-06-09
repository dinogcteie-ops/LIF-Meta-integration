"""Lead pipeline — Phase 4."""
from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import EventType, FollowupStatus, LeadSource, LeadStatus, LostReason
from app.templating import templates

router = APIRouter()

_STATUSES = list(LeadStatus)
_PERIODS = [("all", "All time"), ("today", "Today"),
            ("month", "This month"), ("quarter", "This quarter")]


def _period_window(period: str, today):
    """(start, end) on lead enquiry date (created_at); (None, None) = all time."""
    if period == "today":
        return today, today
    if period == "month":
        return today.replace(day=1), today
    if period == "quarter":
        q = (today.month - 1) // 3
        return date_cls(today.year, q * 3 + 1, 1), today
    return None, None


def _created_date(lead):
    raw = (lead.created_at or "")[:10]
    try:
        return date_cls.fromisoformat(raw)
    except ValueError:
        return None


@router.get("/leads")
def list_leads(
    request: Request,
    status: str = "",
    period: str = "all",
    event_type: str = "",
    source: str = "",
    db: SheetDB = Depends(get_db),
):
    all_leads = db.list_leads()
    if period not in {p for p, _ in _PERIODS}:
        period = "all"
    start, end = _period_window(period, date_cls.today())

    leads = []
    for l in all_leads:
        if status and l.status != status:
            continue
        if event_type and l.event_type != event_type:
            continue
        if source and l.source != source:
            continue
        if start or end:
            d = _created_date(l)
            if d is None or (start and d < start) or (end and d > end):
                continue
        leads.append(l)

    from app.services.reports import lead_funnel, source_conversion
    funnel  = lead_funnel(all_leads)
    sources = source_conversion(all_leads)
    lead_sources = sorted({l.source for l in all_leads if l.source} | {s.value for s in LeadSource})
    return templates.TemplateResponse(
        request, "leads/list.html", {
            "leads":        leads,
            "funnel":       funnel,
            "sources":      sources,
            "statuses":     _STATUSES,
            "event_types":  list(EventType),
            "lead_sources": lead_sources,
            "periods":      _PERIODS,
            "filter_status":     status,
            "filter_period":     period,
            "filter_event_type": event_type,
            "filter_source":     source,
        }
    )


@router.get("/leads/new")
def new_lead_form(request: Request, db: SheetDB = Depends(get_db)):
    return templates.TemplateResponse(
        request, "leads/form.html", {
            "lead":            None,
            "statuses":        _STATUSES,
            "event_types":     list(EventType),
            "sources":         list(LeadSource),
            "followup_statuses": list(FollowupStatus),
            "lost_reasons":    list(LostReason),
        }
    )


@router.post("/leads")
def create_lead(
    request: Request,
    client_name:      str   = Form(...),
    contact:          str   = Form(""),
    event_type:       str   = Form(""),
    tentative_date:   str   = Form(""),
    source:           str   = Form(""),
    status:           str   = Form("new"),
    quoted_amount:    float = Form(0.0),
    notes:            str   = Form(""),
    num_events:       int   = Form(0),
    revised_quote:    float = Form(0.0),
    follow_ups:       str   = Form(""),
    rejection_reason: str   = Form(""),
    meta_campaign:    str   = Form(""),
    referral_name:    str   = Form(""),
    followup_status:  str   = Form("pending"),
    followup_date:    str   = Form(""),
    db: SheetDB = Depends(get_db),
):
    # (#2) A lost lead must have a reason recorded.
    if status == "lost" and not rejection_reason.strip():
        request.session["flash"] = "Please choose a lost reason before marking a lead as Lost."
        return RedirectResponse(url="/leads/new", status_code=303)
    lead = db.create_lead(
        client_name=client_name.strip(),
        contact=contact.strip(),
        event_type=event_type.strip(),
        tentative_date=_parse_date(tentative_date),
        source=source.strip(),
        status=status,
        quoted_amount=quoted_amount,
        notes=notes.strip(),
        num_events=num_events,
        revised_quote=revised_quote,
        follow_ups=follow_ups.strip(),
        rejection_reason=rejection_reason.strip(),
        meta_campaign=meta_campaign.lower() in ("on", "true", "yes", "1"),
        referral_name=referral_name.strip(),
        followup_status=followup_status or "pending",
        followup_date=_parse_date(followup_date),
    )
    return RedirectResponse(url=f"/leads/{lead.id}", status_code=303)


@router.get("/leads/{lead_id}")
def lead_detail(lead_id: int, request: Request, db: SheetDB = Depends(get_db)):
    lead = db.get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404)
    clients_map = {c.id: c for c in db.list_clients()}
    linked_client = clients_map.get(lead.client_id) if lead.client_id else None
    return templates.TemplateResponse(
        request, "leads/detail.html", {
            "lead":              lead,
            "statuses":          _STATUSES,
            "event_types":       list(EventType),
            "sources":           list(LeadSource),
            "linked_client":     linked_client,
            "followup_statuses": list(FollowupStatus),
            "lost_reasons":      list(LostReason),
        }
    )


@router.get("/leads/{lead_id}/edit")
def edit_lead_form(lead_id: int, request: Request, db: SheetDB = Depends(get_db)):
    lead = db.get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "leads/form.html", {
            "lead":              lead,
            "statuses":          _STATUSES,
            "event_types":       list(EventType),
            "sources":           list(LeadSource),
            "followup_statuses": list(FollowupStatus),
            "lost_reasons":      list(LostReason),
        }
    )


@router.post("/leads/{lead_id}")
def update_lead(
    lead_id:          int,
    request:          Request,
    client_name:      str   = Form(...),
    contact:          str   = Form(""),
    event_type:       str   = Form(""),
    tentative_date:   str   = Form(""),
    source:           str   = Form(""),
    status:           str   = Form("new"),
    quoted_amount:    float = Form(0.0),
    notes:            str   = Form(""),
    num_events:       int   = Form(0),
    revised_quote:    float = Form(0.0),
    follow_ups:       str   = Form(""),
    rejection_reason: str   = Form(""),
    meta_campaign:    str   = Form(""),
    referral_name:    str   = Form(""),
    followup_status:  str   = Form("pending"),
    followup_date:    str   = Form(""),
    db: SheetDB = Depends(get_db),
):
    # (#2) A lost lead must have a reason recorded.
    if status == "lost" and not rejection_reason.strip():
        request.session["flash"] = "Please choose a lost reason before marking a lead as Lost."
        return RedirectResponse(url=f"/leads/{lead_id}/edit", status_code=303)
    lead = db.update_lead(
        lead_id,
        client_name=client_name.strip(),
        contact=contact.strip(),
        event_type=event_type.strip(),
        tentative_date=_parse_date(tentative_date),
        source=source.strip(),
        status=status,
        quoted_amount=quoted_amount,
        notes=notes.strip(),
        num_events=num_events,
        revised_quote=revised_quote,
        follow_ups=follow_ups.strip(),
        rejection_reason=rejection_reason.strip(),
        meta_campaign=meta_campaign.lower() in ("on", "true", "yes", "1"),
        referral_name=referral_name.strip(),
        followup_status=followup_status or "pending",
        followup_date=_parse_date(followup_date),
    )
    if lead is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


@router.post("/leads/{lead_id}/delete")
def delete_lead(lead_id: int, db: SheetDB = Depends(get_db)):
    if db.get_lead(lead_id) is None:
        raise HTTPException(status_code=404)
    db.delete_lead(lead_id)
    return RedirectResponse(url="/leads", status_code=303)


@router.post("/leads/{lead_id}/convert")
def convert_lead(
    lead_id: int,
    request: Request,
    db: SheetDB = Depends(get_db),
):
    """Convert a won/quoted lead to a booked event."""
    lead = db.get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404)
    ev = db.create_event(
        name=f"{lead.client_name} – {lead.event_type or 'Event'}",
        client_name=lead.client_name,
        client_id=lead.client_id,
        event_date=lead.tentative_date,
        quoted_amount=lead.quoted_amount,
        status="booked",
        notes=lead.notes or None,
        event_type=lead.event_type or None,
        referral_source=lead.source or None,
    )
    # Mark the lead as won
    db.update_lead(
        lead_id,
        client_name=lead.client_name, contact=lead.contact,
        event_type=lead.event_type, tentative_date=lead.tentative_date,
        source=lead.source, status="won",
        quoted_amount=lead.quoted_amount, notes=lead.notes,
        client_id=lead.client_id,
        num_events=lead.num_events, revised_quote=lead.revised_quote,
        follow_ups=lead.follow_ups, rejection_reason=lead.rejection_reason,
        meta_campaign=lead.meta_campaign, referral_name=lead.referral_name,
        followup_status=lead.followup_status, followup_date=lead.followup_date,
    )
    request.session["flash"] = (
        f"Lead converted to event #{ev.id}. Review and save event details."
    )
    return RedirectResponse(url=f"/events/{ev.id}", status_code=303)


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    return date_cls.fromisoformat(value)
