"""Quick Entry — Sprint 2.

Mobile-friendly quick-add forms for payments and expenses.
"""
from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.database import get_db, SheetDB
from app.enums import CategoryScope, PaymentStatus
from app.rbac import require
from app.templating import templates
from app.validators import parse_amount, parse_date_safe

router = APIRouter(dependencies=[Depends(require("finance.view"))])


@router.get("/quick")
def quick_entry_page(request: Request, db: SheetDB = Depends(get_db)):
    """Mobile-optimized quick entry page."""
    events = sorted(db.list_events(), key=lambda e: e.created_at, reverse=True)[:20]
    categories = db.list_categories(active_only=True)
    return templates.TemplateResponse(
        request,
        "quick.html",
        {
            "events": events,
            "categories": categories,
            "today": date_cls.today().isoformat(),
        },
    )


@router.post("/quick/payment")
def quick_payment(
    event_id: int = Form(...),
    amount: float = Form(...),
    payment_date: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    """Quick-add a payment from mobile."""
    amt, err = parse_amount(amount, "Payment amount")
    if err:
        return RedirectResponse(url="/quick?error=amount", status_code=303)
    pd = parse_date_safe(payment_date, "Payment date")[0] or date_cls.today()
    db.create_payment(
        event_id=event_id,
        amount=amt,
        payment_date=pd,
        notes=notes.strip() or None,
    )
    return RedirectResponse(url="/quick?success=payment", status_code=303)


@router.post("/quick/expense")
def quick_expense(
    category_id: int = Form(...),
    amount: float = Form(...),
    date: str = Form(""),
    event_id: str = Form(""),
    paid_to: str = Form(""),
    notes: str = Form(""),
    db: SheetDB = Depends(get_db),
):
    """Quick-add an expense from mobile."""
    amt, err = parse_amount(amount, "Amount")
    if err:
        return RedirectResponse(url="/quick?error=amount", status_code=303)
    exp_date = parse_date_safe(date, "Expense date")[0] or date_cls.today()
    eid = int(event_id) if event_id.strip() else None
    scope = "event" if eid else "company"
    db.create_expense(
        date_=exp_date,
        scope=scope,
        event_id=eid,
        category_id=category_id,
        amount=amt,
        payment_status="paid",
        paid_amount=amt,
        paid_to=paid_to.strip() or None,
        notes=notes.strip() or None,
    )
    return RedirectResponse(url="/quick?success=expense", status_code=303)
