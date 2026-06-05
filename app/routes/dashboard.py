from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, Request

from app.database import get_db, SheetDB
from app.enums import EventStatus, PaymentStatus
from app.services.analytics import (
    compute_kpis, dashboard_sparklines, event_type_profitability,
    seasonal_analysis, yoy_comparison,
)
from app.services.reports import (
    bank_summary, cash_flow_alerts, event_profits,
    lead_funnel, lost_reason_breakdown, payables_aging, receivables_aging,
    source_conversion,
)
from app.templating import templates

router = APIRouter()


def _range_filter(events, period: str, today: date):
    """Filter event_profits rows by period: month / quarter / year / all."""
    if period == "month":
        return [r for r in events if r.event.event_date
                and r.event.event_date.year == today.year
                and r.event.event_date.month == today.month]
    if period == "quarter":
        qtr = (today.month - 1) // 3 + 1
        start = date(today.year, (qtr - 1) * 3 + 1, 1)
        return [r for r in events if r.event.event_date and r.event.event_date >= start]
    if period == "year":
        return [r for r in events if r.event.event_date
                and r.event.event_date.year == today.year]
    return list(events)  # 'all'


@router.get("/dashboard")
def dashboard(request: Request,
              period: str = "quarter",
              db: SheetDB = Depends(get_db)):
    bank   = bank_summary(db)
    all_ep = event_profits(db)

    today = date.today()

    # Exclude booked events from MTD/QTD profit calculations
    mtd_ep      = [r for r in all_ep if r.event.event_date
                   and r.event.event_date.year == today.year
                   and r.event.event_date.month == today.month
                   and r.event.status != EventStatus.booked]
    mtd_income  = sum(r.income for r in mtd_ep)
    mtd_expense = sum(r.expense for r in mtd_ep)

    qtr       = (today.month - 1) // 3 + 1
    qtr_start = date(today.year, (qtr - 1) * 3 + 1, 1)
    qtd_ep      = [r for r in all_ep if r.event.event_date
                   and r.event.event_date >= qtr_start
                   and r.event.status != EventStatus.booked]
    qtd_income  = sum(r.income for r in qtd_ep)
    qtd_expense = sum(r.expense for r in qtd_ep)

    # Bug D fix: company/personal expenses have no event_id and are invisible in event_profits()
    # Add them as overhead so MTD/QTD profit reflects true spend, not just event-linked costs.
    all_expenses = db.list_expenses()
    mtd_overhead = round(sum(
        e.amount for e in all_expenses
        if e.event_id is None
        and e.date.year == today.year
        and e.date.month == today.month
    ), 2)
    qtd_overhead = round(sum(
        e.amount for e in all_expenses
        if e.event_id is None and e.date >= qtr_start
    ), 2)

    # Total revenue: all non-cancelled events
    total_revenue = round(sum(r.event.quoted_amount for r in all_ep
                              if r.event.status != EventStatus.cancelled), 2)

    # Period-filtered view of events for the table (QW7)
    if period not in ("month", "quarter", "year", "all"):
        period = "quarter"
    filtered = _range_filter(all_ep, period, today)
    filtered.sort(key=lambda r: (r.event.event_date or date.min), reverse=True)

    # Cash-flow alerts banner (Phase 1.4)
    alerts = cash_flow_alerts(db, today)

    # Lead funnel widget (Phase 4)
    all_leads = db.list_leads()
    funnel    = lead_funnel(all_leads)
    sources   = source_conversion(all_leads)
    lost      = lost_reason_breakdown(all_leads)

    # Sidebar overdue counts (QW3 — keep cheap by reusing aging compute)
    _, rec_totals = receivables_aging(db, today)
    _, pay_totals = payables_aging(db, today)
    sidebar_badges = {
        "receivables_overdue": (rec_totals.bucket_0_30_count
                                + rec_totals.bucket_31_60_count
                                + rec_totals.bucket_60_plus_count),
        "payables_overdue": (pay_totals.bucket_0_30_count
                             + pay_totals.bucket_31_60_count
                             + pay_totals.bucket_60_plus_count),
    }

    # Sprint 1 & 5: Advanced KPIs and sparklines
    kpis = compute_kpis(db, today)
    sparklines = dashboard_sparklines(db, today)
    type_profit = event_type_profitability(db)
    yoy = yoy_comparison(db, today)
    seasonal = seasonal_analysis(db)

    # ── Drill-down data for stat-card modals ──────────────────────────────────
    cats_map = {c.id: c.name for c in db.list_categories()}

    # Paid expenses — scope totals + per-line detail for bank balance modal
    paid_by_scope: dict[str, float] = defaultdict(float)
    paid_expenses_detail: dict[str, list] = {"event": [], "company": [], "personal": []}
    event_name_map = {r.event.id: r.event.name for r in all_ep}
    for e in all_expenses:
        if e.payment_status == PaymentStatus.paid:
            paid = e.amount
        elif e.payment_status == PaymentStatus.partial:
            paid = e.paid_amount or 0.0
        else:
            continue  # pending — skip
        paid_by_scope[e.scope.value] += paid
        scope = e.scope.value
        if scope in paid_expenses_detail:
            paid_expenses_detail[scope].append({
                "date": e.date,
                "cat_name": cats_map.get(e.category_id, "?"),
                "paid": paid,
                "paid_to": e.paid_to or "",
                "event_name": event_name_map.get(e.event_id, "") if e.event_id else "",
                "status": e.payment_status.value,
            })
    for scope in paid_expenses_detail:
        paid_expenses_detail[scope].sort(key=lambda x: x["date"], reverse=True)

    # Overhead expenses enriched with category names — MTD / QTD modals
    def _enrich_overhead(exps):
        return sorted([
            {
                "date": e.date,
                "cat_name": cats_map.get(e.category_id, "?"),
                "scope": e.scope.value,
                "amount": e.amount,
                "status": e.payment_status.value,
                "paid_to": e.paid_to or "",
            }
            for e in exps if e.event_id is None
        ], key=lambda x: x["date"], reverse=True)

    mtd_overhead_exps = _enrich_overhead([
        e for e in all_expenses
        if e.event_id is None
        and e.date.year == today.year
        and e.date.month == today.month
    ])
    qtd_overhead_exps = _enrich_overhead([
        e for e in all_expenses
        if e.event_id is None and e.date >= qtr_start
    ])

    # Total revenue grouped by status — total revenue modal
    rev_by_status: dict[str, dict] = {}
    for r in all_ep:
        s = r.event.status.value
        if s not in rev_by_status:
            rev_by_status[s] = {"count": 0, "quoted": 0.0, "received": 0.0}
        rev_by_status[s]["count"]    += 1
        rev_by_status[s]["quoted"]   += r.event.quoted_amount
        rev_by_status[s]["received"] += r.income

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "bank": bank,
            "mtd": {"income": mtd_income, "expense": mtd_expense, "profit": round(mtd_income - mtd_expense - mtd_overhead, 2), "event_expense": mtd_expense, "overhead": mtd_overhead},
            "qtd": {"income": qtd_income, "expense": qtd_expense, "profit": round(qtd_income - qtd_expense - qtd_overhead, 2), "event_expense": qtd_expense, "overhead": qtd_overhead},
            "total_revenue": total_revenue,
            "recent_events": filtered,
            "period": period,
            "today": today,
            "alerts":         alerts,
            "sidebar_badges": sidebar_badges,
            "funnel":         funnel,
            "sources":        sources,
            "lost":           lost,
            # Sprint 1 & 5 analytics
            "kpis":           kpis,
            "sparklines":     sparklines,
            "type_profit":    type_profit,
            "yoy":            yoy,
            "seasonal":       seasonal,
            # Drill-down data
            "all_ep":              all_ep,
            "mtd_ep":              mtd_ep,
            "qtd_ep":              qtd_ep,
            "mtd_overhead_exps":   mtd_overhead_exps,
            "qtd_overhead_exps":   qtd_overhead_exps,
            "paid_by_scope":       dict(paid_by_scope),
            "paid_expenses_detail": paid_expenses_detail,
            "rev_by_status":       rev_by_status,
        },
    )
