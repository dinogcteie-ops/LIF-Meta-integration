from datetime import date as date_cls
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import CategoryScope, PaymentStatus, PaymentType
from app.services.analytics import expense_analytics as _expense_analytics
from app.rbac import require
from app.templating import templates

router = APIRouter(dependencies=[Depends(require("finance.view"))])


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
    import json
    from collections import defaultdict

    analytics = _expense_analytics(db)
    grand = analytics.grand_total or 1

    # ── Fix: rebuild chart data grouped by expense.scope (matches by_scope) ──
    # analytics.by_category groups by category.scope which can differ from
    # expense.scope — that caused the card vs modal value mismatch.
    all_expenses = db.list_expenses()
    cats_map     = {c.id: c for c in db.list_categories()}

    scope_cats: dict[str, dict] = defaultdict(dict)  # scope → cat_name → row
    for e in all_expenses:
        s   = e.scope.value                          # use expense's scope
        cat = cats_map.get(e.category_id)
        name = cat.name if cat else f"Cat #{e.category_id}"
        if name not in scope_cats[s]:
            scope_cats[s][name] = {"name": name, "scope": s,
                                   "total_amount": 0.0, "paid_amount": 0.0,
                                   "pending_amount": 0.0, "txn_count": 0}
        row = scope_cats[s][name]
        paid = e.paid_amount if e.payment_status.value == "paid" else (
               e.paid_amount or 0.0 if e.payment_status.value == "partial" else 0.0)
        pending = max(0.0, e.amount - paid) if e.payment_status.value != "paid" else 0.0
        row["total_amount"]   += e.amount
        row["paid_amount"]    += paid
        row["pending_amount"] += pending
        row["txn_count"]      += 1

    cat_json = json.dumps([
        {**r, "total_amount": round(r["total_amount"], 2),
               "paid_amount": round(r["paid_amount"], 2),
               "pending_amount": round(r["pending_amount"], 2)}
        for s in scope_cats
        for r in sorted(scope_cats[s].values(), key=lambda x: -x["total_amount"])
    ])

    # ── Pending split: active vs booked vs other events ───────────────────────
    events_map = {e.id: e for e in db.list_events()}
    pending_by_status: dict[str, float] = defaultdict(float)
    for e in all_expenses:
        if e.payment_status.value == "pending":
            ev = events_map.get(e.event_id) if e.event_id else None
            label = ev.status.value if ev else "overhead"
            pending_by_status[label] += e.amount
        elif e.payment_status.value == "partial":
            remaining = max(0.0, e.amount - (e.paid_amount or 0.0))
            if remaining > 0:
                ev = events_map.get(e.event_id) if e.event_id else None
                label = ev.status.value if ev else "overhead"
                pending_by_status[label] += remaining

    pending_split = {k: round(v, 2) for k, v in pending_by_status.items()}

    return templates.TemplateResponse(
        request,
        "expenses/analytics.html",
        {"analytics": analytics, "grand": grand,
         "cat_json": cat_json, "pending_split": pending_split},
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
    if status in (PaymentStatus.pending, PaymentStatus.estimated):
        return 0.0
    return paid_amount   # partial
