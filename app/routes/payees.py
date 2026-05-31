"""Payee directory (freelancers + vendors) — Phase 2."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.services.reports import top_payees
from app.templating import templates

PAYEE_TYPES = ["freelancer", "vendor", "other"]

router = APIRouter()


@router.get("/payees")
def list_payees(request: Request, db: SheetDB = Depends(get_db)):
    payees = db.list_payees()
    payees_map = {p.id: p for p in payees}
    expenses = db.list_expenses()
    stats = top_payees(expenses, payees_map, n=999)
    stats_by_pid = {s.payee_id: s for s in stats if s.payee_id}
    return templates.TemplateResponse(
        request, "payees/list.html", {
            "payees": payees,
            "stats_by_pid": stats_by_pid,
            "payee_types": PAYEE_TYPES,
        }
    )


@router.get("/payees/new")
def new_payee_form(request: Request):
    return templates.TemplateResponse(
        request, "payees/form.html", {
            "payee": None,
            "payee_types": PAYEE_TYPES,
        }
    )


@router.post("/payees")
def create_payee(
    request: Request,
    name: str = Form(...),
    payee_type: str = Form("freelancer"),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    p = db.create_payee(
        name=name.strip(),
        payee_type=payee_type,
        phone=phone.strip() or None,
        email=email.strip() or None,
        notes=notes.strip() or None,
    )
    return RedirectResponse(url=f"/payees/{p.id}", status_code=303)


@router.get("/payees/{payee_id}")
def payee_detail(payee_id: int, request: Request, db: SheetDB = Depends(get_db)):
    payee = db.get_payee(payee_id)
    if payee is None:
        raise HTTPException(status_code=404, detail="Payee not found")

    # Gather expenses for this payee:
    # 1) linked by payee_id  2) legacy free-text match on paid_to
    all_expenses = db.list_expenses()
    all_events   = {e.id: e for e in db.list_events()}
    cats         = {c.id: c for c in db.list_categories()}
    linked_exps  = []
    for exp in all_expenses:
        if (exp.payee_id == payee_id) or \
           (exp.payee_id is None and exp.paid_to
                and exp.paid_to.lower() == payee.name.lower()):
            exp.category = cats.get(exp.category_id)
            exp.event    = all_events.get(exp.event_id) if exp.event_id else None
            linked_exps.append(exp)

    total_spent = round(sum(e.amount for e in linked_exps), 2)
    total_paid  = round(sum(
        e.amount if e.payment_status.value == "paid" else (e.paid_amount or 0.0)
        for e in linked_exps
    ), 2)
    total_pending = round(max(0.0, total_spent - total_paid), 2)

    return templates.TemplateResponse(
        request, "payees/detail.html", {
            "payee": payee,
            "expenses": linked_exps,
            "total_spent": total_spent,
            "total_paid": total_paid,
            "total_pending": total_pending,
            "payee_types": PAYEE_TYPES,
        }
    )


@router.get("/payees/{payee_id}/edit")
def edit_payee_form(payee_id: int, request: Request, db: SheetDB = Depends(get_db)):
    payee = db.get_payee(payee_id)
    if payee is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "payees/form.html", {
            "payee": payee,
            "payee_types": PAYEE_TYPES,
        }
    )


@router.post("/payees/{payee_id}")
def update_payee(
    payee_id: int,
    name: str = Form(...),
    payee_type: str = Form("freelancer"),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    p = db.update_payee(
        payee_id,
        name=name.strip(),
        payee_type=payee_type,
        phone=phone.strip() or None,
        email=email.strip() or None,
        notes=notes.strip() or None,
    )
    if p is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(url=f"/payees/{payee_id}", status_code=303)


@router.post("/payees/{payee_id}/delete")
def delete_payee(payee_id: int, db: SheetDB = Depends(get_db)):
    if db.get_payee(payee_id) is None:
        raise HTTPException(status_code=404)
    db.delete_payee(payee_id)
    return RedirectResponse(url="/payees", status_code=303)
