from datetime import date

from fastapi import APIRouter, Depends, Request

from app.database import get_db, SheetDB
from app.services.reports import (
    budget_vs_actual, event_profits, expense_breakdown, monthly_history,
    project_next, quarterly_history, top_clients, top_payees,
)
from app.rbac import require
from app.templating import templates

router = APIRouter(dependencies=[Depends(require("finance.view"))])


@router.get("/reports")
def reports_page(request: Request, db: SheetDB = Depends(get_db)):
    history    = monthly_history(db, months=6)
    projection = project_next(history, months=3)
    quarters   = quarterly_history(db, quarters=4)
    events     = event_profits(db)
    breakdown  = expense_breakdown(db)

    # Phase 2: directory analytics (reuse already-loaded data)
    clients_map  = {c.id: c for c in db.list_clients()}
    payees_map   = {p.id: p for p in db.list_payees()}
    all_expenses = db.list_expenses()
    top_clients_data = top_clients(events, clients_map, n=8)
    top_payees_data  = top_payees(all_expenses, payees_map, n=8)

    # Sprint 6: budget vs actual for current month
    today   = date.today()
    budgets = budget_vs_actual(db, today.year, today.month)
    budget_month_label = today.strftime("%B %Y")

    chart_labels  = [r.period for r in history] + [r.period for r in projection]
    chart_income  = [r.income for r in history] + [r.income for r in projection]
    chart_expense = [r.expense for r in history] + [r.expense for r in projection]
    chart_profit  = [r.profit for r in history] + [r.profit for r in projection]

    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "history": history,
            "projection": projection,
            "quarters": quarters,
            "events": events,
            "breakdown": breakdown,
            "top_clients": top_clients_data,
            "top_payees": top_payees_data,
            "budgets": budgets,
            "budget_month_label": budget_month_label,
            "chart": {
                "labels": chart_labels,
                "income": chart_income,
                "expense": chart_expense,
                "profit": chart_profit,
                "history_len": len(history),
            },
        },
    )
