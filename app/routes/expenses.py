from datetime import date as date_cls
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import CategoryScope, PaymentStatus, PaymentType
from app.services.analytics import expense_analytics as _expense_analytics
from app.templating import templates

router = APIRouter()


@router.get("/expenses")
def list_expenses(
    request: Request,
    db: SheetDB = Depends(get_db),
    event_id: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    # Normalise every filter — the GET form always submits empty strings for
    # unselected dropdowns; treat "" the same as "not provided" (i.e. None).
    eid    = int(event_id)    if (event_id    or "").strip().isdigit() else None
    cid    = int(category_id) if (category_id or "").strip().isdigit() else None
    scope  = scope  or None   # "" → None  (avoids filtering by empty string)
    status = status or None   # "" → None
    expenses = db.list_expenses(
        event_id=eid,
        category_id=cid,
        scope=scope,
        status=status,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
    )
    events     = {e.id: e for e in db.list_events()}
    categories = {c.id: c for c in db.list_categories()}
    total = round(sum(e.amount for e in expenses), 2)
    paid_total = round(
        sum(
            (e.amount if e.payment_status == PaymentStatus.paid else (e.paid_amount or 0.0))
            for e in expenses
            if e.payment_status != PaymentStatus.pending
        ),
        2,
    )
    return templates.TemplateResponse(
        request,
        "expenses/list.html",
        {
            "expenses": expenses,
            "events": events,
            "categories": categories,
            "all_events": list(events.values()),
            "all_categories": list(categories.values()),
            "scopes": list(CategoryScope),
            "statuses": list(PaymentStatus),
            "filters": {
                "event_id": eid,
                "category_id": cid,
                "scope": scope,
                "status": status,
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
            "total": total,
            "paid_total": paid_total,
        },
    )


@router.get("/expenses/new")
def new_expense_form(
    request: Request,
    db: SheetDB = Depends(get_db),
    event_id: Optional[int] = Query(None),
):
    events     = sorted(db.list_events(), key=lambda e: e.created_at, reverse=True)
    categories = db.list_categories(active_only=True)
    payees     = db.list_payees()
    return templates.TemplateResponse(
        request,
        "expenses/form.html",
        {
            "expense": None,
            "events": events,
            "categories": categories,
            "scopes": list(CategoryScope),
            "statuses": list(PaymentStatus),
            "payment_types": list(PaymentType),
            "preselect_event_id": event_id,
            "payees": payees,
        },
    )


@router.post("/expenses")
def create_expense(
    request: Request,
    date: str = Form(...),
    scope: str = Form(...),
    event_id: str = Form(""),
    category_id: int = Form(...),
    amount: float = Form(...),
    payment_status: str = Form("paid"),
    paid_amount: float = Form(0.0),
    paid_to: str = Form(""),
    payee_id: str = Form(""),
    notes: str = Form(""),
    is_recurring: str = Form(""),
    recurring_day: str = Form(""),
    payment_type: Optional[str] = Form(None),
    db: SheetDB = Depends(get_db),
):
    scope_val = CategoryScope(scope)
    eid = int(event_id) if event_id else None
    if scope_val != CategoryScope.event:
        eid = None
    ps = PaymentStatus(payment_status)
    paid_amt = _resolve_paid_amount(ps, amount, paid_amount)
    pid = int(payee_id) if payee_id.strip() else None
    rec = is_recurring.lower() in ("true", "on", "1", "yes")
    rday = int(recurring_day) if recurring_day.strip() and rec else None
    pt = (payment_type or "").strip() or None
    exp = db.create_expense(
        date_=date_cls.fromisoformat(date),
        scope=scope_val.value,
        event_id=eid,
        category_id=category_id,
        amount=amount,
        payment_status=ps.value,
        paid_amount=paid_amt,
        paid_to=paid_to.strip() or None,
        notes=notes.strip() or None,
        payee_id=pid,
        is_recurring=rec,
        recurring_day=rday,
        payment_type=pt,
    )
    if eid:
        return RedirectResponse(url=f"/events/{eid}", status_code=303)
    return RedirectResponse(url="/expenses", status_code=303)


@router.get("/expenses/analytics")
def expense_analytics_page(request: Request, db: SheetDB = Depends(get_db)):
    analytics = _expense_analytics(db)
    grand = analytics.grand_total or 1  # avoid div/0 in template
    return templates.TemplateResponse(
        request,
        "expenses/analytics.html",
        {"analytics": analytics, "grand": grand},
    )


@router.get("/expenses/{expense_id}/edit")
def edit_expense_form(expense_id: int, request: Request, db: SheetDB = Depends(get_db)):
    exp = db.get_expense(expense_id)
    if exp is None:
        raise HTTPException(status_code=404)
    events     = sorted(db.list_events(), key=lambda e: e.created_at, reverse=True)
    categories = db.list_categories(active_only=True)
    payees     = db.list_payees()
    return templates.TemplateResponse(
        request,
        "expenses/form.html",
        {
            "expense": exp,
            "events": events,
            "categories": categories,
            "scopes": list(CategoryScope),
            "statuses": list(PaymentStatus),
            "payment_types": list(PaymentType),
            "payees": payees,
        },
    )


@router.post("/expenses/{expense_id}")
def update_expense(
    expense_id: int,
    date: str = Form(...),
    scope: str = Form(...),
    event_id: str = Form(""),
    category_id: int = Form(...),
    amount: float = Form(...),
    payment_status: str = Form("paid"),
    paid_amount: float = Form(0.0),
    paid_to: str = Form(""),
    payee_id: str = Form(""),
    notes: str = Form(""),
    is_recurring: str = Form(""),
    recurring_day: str = Form(""),
    payment_type: Optional[str] = Form(None),
    db: SheetDB = Depends(get_db),
):
    exp = db.get_expense(expense_id)
    if exp is None:
        raise HTTPException(status_code=404)
    scope_val = CategoryScope(scope)
    eid = int(event_id) if event_id else None
    if scope_val != CategoryScope.event:
        eid = None
    ps = PaymentStatus(payment_status)
    pid = int(payee_id) if payee_id.strip() else None
    rec = is_recurring.lower() in ("true", "on", "1", "yes")
    rday = int(recurring_day) if recurring_day.strip() and rec else None
    pt = (payment_type or "").strip() or None
    db.update_expense(
        expense_id,
        date_=date_cls.fromisoformat(date),
        scope=scope_val.value,
        event_id=eid,
        category_id=category_id,
        amount=amount,
        payment_status=ps.value,
        paid_amount=_resolve_paid_amount(ps, amount, paid_amount),
        paid_to=paid_to.strip() or None,
        notes=notes.strip() or None,
        payee_id=pid,
        is_recurring=rec,
        recurring_day=rday,
        payment_type=pt,
    )
    if eid:
        return RedirectResponse(url=f"/events/{eid}", status_code=303)
    return RedirectResponse(url="/expenses", status_code=303)


@router.post("/expenses/{expense_id}/delete")
def delete_expense(expense_id: int, db: SheetDB = Depends(get_db)):
    exp = db.get_expense(expense_id)
    if exp is None:
        raise HTTPException(status_code=404)
    event_id = exp.event_id
    db.delete_expense(expense_id)
    if event_id:
        return RedirectResponse(url=f"/events/{event_id}", status_code=303)
    return RedirectResponse(url="/expenses", status_code=303)


def _parse_date(value: Optional[str]):
    if not value:
        return None
    return date_cls.fromisoformat(value)


def _resolve_paid_amount(status: PaymentStatus, amount: float, paid_amount: float) -> float:
    if status == PaymentStatus.paid:
        return amount
    if status == PaymentStatus.pending:
        return 0.0
    return paid_amount
