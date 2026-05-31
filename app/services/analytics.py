"""Advanced analytics service — Sprint 1 & 5.

Provides KPI calculations, YoY comparisons, event-type profitability,
seasonal heatmaps, and sparkline data.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.database import SheetDB
from app.domain import Event, EventPayment, Expense
from app.enums import EventStatus, PaymentStatus


# ─── KPI Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class KPICard:
    """A single KPI metric for the dashboard."""
    label: str
    value: float
    formatted: str
    trend: float = 0.0        # percentage change vs prior period
    trend_label: str = ""     # e.g. "vs last month"
    sparkline: list = field(default_factory=list)  # last N data points
    icon: str = ""
    color: str = ""           # CSS variable name


@dataclass
class EventTypeProfit:
    """Profit analytics per event type."""
    event_type: str
    event_count: int
    total_quoted: float
    total_expense: float
    total_received: float
    avg_quoted: float
    avg_profit: float
    avg_margin_pct: float
    total_profit: float


@dataclass
class YoYComparison:
    """Year-over-year comparison for a metric."""
    period_label: str       # e.g. "Q2 2026"
    current_value: float
    prior_value: float
    change_pct: float
    change_abs: float


@dataclass
class SeasonalData:
    """Monthly booking/revenue density for seasonal analysis."""
    month: int
    month_name: str
    event_count: int
    total_revenue: float
    avg_revenue: float


@dataclass
class CategoryExpenseRow:
    """Expense total for a single category."""
    name: str
    scope: str              # event | company | personal
    total_amount: float
    paid_amount: float
    pending_amount: float
    monthly_budget: float   # 0.0 if not set
    budget_pct: float       # total_amount / monthly_budget * 100; 0 if no budget
    txn_count: int


@dataclass
class ExpenseAnalytics:
    """Full expense analytics breakdown for the analytics dashboard."""
    grand_total: float
    grand_paid: float
    grand_pending: float
    grand_partial_outstanding: float
    by_scope: dict          # scope → total amount
    by_scope_paid: dict     # scope → paid amount
    by_category: list       # list[CategoryExpenseRow], sorted by total_amount desc
    by_payment_type: list   # list[tuple(type_name, total_amount, count)]
    monthly_trend: list     # list[tuple(label, event_amt, company_amt, personal_amt)]


# ─── Analytics Functions ─────────────────────────────────────────────────────

def compute_kpis(db: SheetDB, today: date = None) -> list[KPICard]:
    """Compute key performance indicators for the KPI dashboard."""
    today = today or date.today()
    events = db.list_events()
    payments = db.list_payments()
    expenses_all = db.list_expenses()

    # Current month & prior month
    cm_start = date(today.year, today.month, 1)
    if today.month == 1:
        pm_start = date(today.year - 1, 12, 1)
        pm_end = date(today.year, 1, 1) - timedelta(days=1)
    else:
        pm_start = date(today.year, today.month - 1, 1)
        pm_end = cm_start - timedelta(days=1)

    # Monthly revenue (payments received this month)
    cm_revenue = sum(p.amount for p in payments if p.payment_date >= cm_start and p.payment_date <= today)
    pm_revenue = sum(p.amount for p in payments if p.payment_date >= pm_start and p.payment_date <= pm_end)
    revenue_trend = ((cm_revenue - pm_revenue) / pm_revenue * 100) if pm_revenue > 0 else 0

    # Monthly bookings count
    cm_bookings = len([e for e in events if e.created_at and _parse_created_month(e.created_at, today)])
    pm_bookings = len([e for e in events if e.created_at and _parse_created_month(e.created_at, pm_start)])

    # Average event value
    active_events = [e for e in events if e.status != EventStatus.cancelled and e.quoted_amount > 0]
    avg_event_value = sum(e.quoted_amount for e in active_events) / len(active_events) if active_events else 0

    # Average profit margin — Bug B fix: include ALL expense scopes (company + personal overhead)
    total_quoted = sum(e.quoted_amount for e in active_events)
    total_received_all = sum(p.amount for p in payments)
    total_exp = sum(ex.amount for ex in expenses_all)   # was: if ex.event_id — now includes all
    avg_margin = ((total_received_all - total_exp) / total_received_all * 100) if total_received_all > 0 else 0

    # Booking pace (events per month over last 6 months)
    six_months_ago = date(today.year, today.month - 6, 1) if today.month > 6 else date(today.year - 1, today.month + 6, 1)
    recent_events = [e for e in events if e.event_date and e.event_date >= six_months_ago]
    booking_pace = len(recent_events) / 6.0

    # Monthly sparkline data (last 6 months revenue)
    sparkline_revenue = _monthly_sparkline(payments, today, 6)
    sparkline_bookings = _monthly_booking_sparkline(events, today, 6)

    # Collection rate
    total_received = sum(p.amount for p in payments)
    total_invoiced = sum(e.quoted_amount for e in active_events)
    collection_rate = (total_received / total_invoiced * 100) if total_invoiced > 0 else 0

    kpis = [
        KPICard(
            label="Monthly Revenue",
            value=cm_revenue,
            formatted=_fmt_money(cm_revenue),
            trend=round(revenue_trend, 1),
            trend_label="vs last month",
            sparkline=sparkline_revenue,
            icon="bi-graph-up",
            color="lif-green",
        ),
        KPICard(
            label="Avg Event Value",
            value=avg_event_value,
            formatted=_fmt_money(avg_event_value),
            trend=0,
            trend_label="all-time avg",
            sparkline=[],
            icon="bi-diamond",
            color="lif-gold",
        ),
        KPICard(
            label="Profit Margin",
            value=avg_margin,
            formatted=f"{avg_margin:.1f}%",
            trend=0,
            trend_label="overall",
            sparkline=[],
            icon="bi-percent",
            color="lif-olive",
        ),
        KPICard(
            label="Booking Pace",
            value=booking_pace,
            formatted=f"{booking_pace:.1f}/mo",
            trend=0,
            trend_label="last 6 months",
            sparkline=sparkline_bookings,
            icon="bi-calendar-plus",
            color="lif-blue",
        ),
        KPICard(
            label="Collection Rate",
            value=collection_rate,
            formatted=f"{collection_rate:.1f}%",
            trend=0,
            trend_label="total received / invoiced",
            sparkline=[],
            icon="bi-cash-coin",
            color="lif-amber",
        ),
    ]
    return kpis


def event_type_profitability(db: SheetDB) -> list[EventTypeProfit]:
    """Compute profitability breakdown by event type."""
    events = db.list_events()
    payments = db.list_payments()
    expenses_all = db.list_expenses()

    # Build lookup maps
    payments_by_event: dict[int, float] = defaultdict(float)
    for p in payments:
        payments_by_event[p.event_id] += p.amount

    expenses_by_event: dict[int, float] = defaultdict(float)
    for e in expenses_all:
        if e.event_id:
            expenses_by_event[e.event_id] += e.amount

    # Group by event type
    type_data: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "total_quoted": 0.0, "total_expense": 0.0, "total_received": 0.0
    })

    for ev in events:
        if ev.status == EventStatus.cancelled:
            continue
        etype = ev.event_type or "Other"
        type_data[etype]["count"] += 1
        type_data[etype]["total_quoted"] += ev.quoted_amount
        type_data[etype]["total_expense"] += expenses_by_event.get(ev.id, 0)
        type_data[etype]["total_received"] += payments_by_event.get(ev.id, 0)

    results = []
    for etype, d in type_data.items():
        count = d["count"]
        total_quoted = d["total_quoted"]
        total_expense = d["total_expense"]
        total_received = d["total_received"]
        total_profit = total_received - total_expense
        avg_quoted = total_quoted / count if count else 0
        avg_profit = total_profit / count if count else 0
        # Bug C fix: use total_quoted as denominator so 0-payment event types show meaningful margin
        avg_margin = ((total_received - total_expense) / total_quoted * 100) if total_quoted > 0 else 0

        results.append(EventTypeProfit(
            event_type=etype,
            event_count=count,
            total_quoted=round(total_quoted, 2),
            total_expense=round(total_expense, 2),
            total_received=round(total_received, 2),
            avg_quoted=round(avg_quoted, 2),
            avg_profit=round(avg_profit, 2),
            avg_margin_pct=round(avg_margin, 1),
            total_profit=round(total_profit, 2),
        ))

    results.sort(key=lambda x: -x.total_profit)
    return results


def yoy_comparison(db: SheetDB, today: date = None) -> list[YoYComparison]:
    """Generate year-over-year quarterly comparisons."""
    today = today or date.today()
    payments = db.list_payments()

    comparisons = []
    # Compare last 4 quarters with the same quarter a year ago
    for q_offset in range(4):
        # Current period
        cur_q_start, cur_q_end, cur_label = _quarter_range(today, q_offset)
        # Same quarter last year
        prior_q_start = date(cur_q_start.year - 1, cur_q_start.month, cur_q_start.day)
        prior_q_end = date(cur_q_end.year - 1, cur_q_end.month, cur_q_end.day)
        prior_label = f"Q{(cur_q_start.month - 1) // 3 + 1} {cur_q_start.year - 1}"

        cur_revenue = sum(p.amount for p in payments
                         if cur_q_start <= p.payment_date <= cur_q_end)
        prior_revenue = sum(p.amount for p in payments
                           if prior_q_start <= p.payment_date <= prior_q_end)

        change_abs = cur_revenue - prior_revenue
        change_pct = (change_abs / prior_revenue * 100) if prior_revenue > 0 else (100 if cur_revenue > 0 else 0)

        comparisons.append(YoYComparison(
            period_label=cur_label,
            current_value=round(cur_revenue, 2),
            prior_value=round(prior_revenue, 2),
            change_pct=round(change_pct, 1),
            change_abs=round(change_abs, 2),
        ))

    comparisons.reverse()
    return comparisons


def seasonal_analysis(db: SheetDB) -> list[SeasonalData]:
    """Compute seasonal booking/revenue patterns by month."""
    events = db.list_events()
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    monthly: dict[int, dict] = {m: {"count": 0, "revenue": 0.0} for m in range(1, 13)}

    for ev in events:
        if ev.event_date and ev.status != EventStatus.cancelled:
            m = ev.event_date.month
            monthly[m]["count"] += 1
            monthly[m]["revenue"] += ev.quoted_amount

    results = []
    for m in range(1, 13):
        count = monthly[m]["count"]
        revenue = monthly[m]["revenue"]
        avg = revenue / count if count > 0 else 0
        results.append(SeasonalData(
            month=m,
            month_name=month_names[m],
            event_count=count,
            total_revenue=round(revenue, 2),
            avg_revenue=round(avg, 2),
        ))
    return results


def expense_analytics(db: SheetDB, today: date = None) -> "ExpenseAnalytics":
    """Compute expense breakdown by scope, category, payment type, and monthly trend."""
    today = today or date.today()
    expenses = db.list_expenses()
    cats = {c.id: c for c in db.list_categories()}

    # ── helpers ────────────────────────────────────────────────────────────────
    def _paid(e: Expense) -> float:
        if e.payment_status == PaymentStatus.paid:
            return e.amount
        if e.payment_status == PaymentStatus.partial:
            return e.paid_amount or 0.0
        return 0.0

    def _pending(e: Expense) -> float:
        if e.payment_status == PaymentStatus.paid:
            return 0.0
        if e.payment_status == PaymentStatus.pending:
            return e.amount
        return max(0.0, e.amount - (e.paid_amount or 0.0))  # partial

    # ── accumulators ──────────────────────────────────────────────────────────
    grand_total = 0.0
    grand_paid = 0.0
    grand_pending = 0.0
    grand_partial_outstanding = 0.0

    by_scope: dict[str, float] = {}
    by_scope_paid: dict[str, float] = {}

    # cat_id → {total, paid, pending, budget, count}
    cat_totals: dict[int, dict] = {}

    # payment_type → {amount, count}
    pt_totals: dict[str, dict] = {}

    # (year, month) → {scope → amount}
    monthly: dict[tuple, dict] = {}

    for e in expenses:
        paid = _paid(e)
        pending_amt = _pending(e)
        partial_out = max(0.0, e.amount - paid) if e.payment_status == PaymentStatus.partial else 0.0

        grand_total += e.amount
        grand_paid  += paid
        grand_pending += pending_amt
        grand_partial_outstanding += partial_out

        scope = e.scope.value
        by_scope[scope] = by_scope.get(scope, 0.0) + e.amount
        by_scope_paid[scope] = by_scope_paid.get(scope, 0.0) + paid

        # Category aggregation
        cid = e.category_id
        if cid not in cat_totals:
            cat = cats.get(cid)
            cat_totals[cid] = {
                "name": cat.name if cat else f"Cat #{cid}",
                "scope": cat.scope.value if cat else scope,
                "monthly_budget": cat.monthly_budget if cat else 0.0,
                "total": 0.0, "paid": 0.0, "pending": 0.0, "count": 0,
            }
        cat_totals[cid]["total"]   += e.amount
        cat_totals[cid]["paid"]    += paid
        cat_totals[cid]["pending"] += pending_amt
        cat_totals[cid]["count"]   += 1

        # Payment type aggregation
        pt = (e.payment_type or "").strip() or "Unspecified"
        if pt not in pt_totals:
            pt_totals[pt] = {"amount": 0.0, "count": 0}
        pt_totals[pt]["amount"] += e.amount
        pt_totals[pt]["count"]  += 1

        # Monthly trend (last 6 months)
        key = (e.date.year, e.date.month)
        if key not in monthly:
            monthly[key] = {"event": 0.0, "company": 0.0, "personal": 0.0}
        if scope in monthly[key]:
            monthly[key][scope] += e.amount

    # ── build monthly trend (last 6 months) ──────────────────────────────────
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    trend = []
    for i in range(5, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12; y -= 1
        label = f"{month_names[m]} {str(y)[2:]}"
        d = monthly.get((y, m), {})
        trend.append((label, round(d.get("event", 0.0), 2),
                      round(d.get("company", 0.0), 2),
                      round(d.get("personal", 0.0), 2)))

    # ── assemble category rows ────────────────────────────────────────────────
    cat_rows = []
    for cid, d in sorted(cat_totals.items(), key=lambda x: -x[1]["total"]):
        budget = d["monthly_budget"]
        budget_pct = (d["total"] / budget * 100) if budget > 0 else 0.0
        cat_rows.append(CategoryExpenseRow(
            name=d["name"],
            scope=d["scope"],
            total_amount=round(d["total"], 2),
            paid_amount=round(d["paid"], 2),
            pending_amount=round(d["pending"], 2),
            monthly_budget=budget,
            budget_pct=round(budget_pct, 1),
            txn_count=d["count"],
        ))

    # ── assemble payment type rows ────────────────────────────────────────────
    pt_rows = sorted(
        [(pt, round(v["amount"], 2), v["count"]) for pt, v in pt_totals.items()],
        key=lambda x: -x[1],
    )

    return ExpenseAnalytics(
        grand_total=round(grand_total, 2),
        grand_paid=round(grand_paid, 2),
        grand_pending=round(grand_pending, 2),
        grand_partial_outstanding=round(grand_partial_outstanding, 2),
        by_scope={k: round(v, 2) for k, v in by_scope.items()},
        by_scope_paid={k: round(v, 2) for k, v in by_scope_paid.items()},
        by_category=cat_rows,
        by_payment_type=pt_rows,
        monthly_trend=trend,
    )


def dashboard_sparklines(db: SheetDB, today: date = None) -> dict:
    """Generate sparkline data for the enhanced dashboard."""
    today = today or date.today()
    payments = db.list_payments()
    expenses_all = db.list_expenses()

    return {
        "revenue_6m": _monthly_sparkline(payments, today, 6),
        "expenses_6m": _monthly_expense_sparkline(expenses_all, today, 6),
        "profit_6m": _monthly_profit_sparkline(payments, expenses_all, today, 6),
    }


# ─── Helper Functions ────────────────────────────────────────────────────────

def _monthly_sparkline(payments: list, today: date, months: int) -> list[float]:
    """Get monthly revenue totals for the last N months."""
    data = []
    for i in range(months - 1, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        month_total = sum(p.amount for p in payments
                         if p.payment_date.year == y and p.payment_date.month == m)
        data.append(round(month_total, 2))
    return data


def _monthly_expense_sparkline(expenses: list, today: date, months: int) -> list[float]:
    """Get monthly expense totals for the last N months."""
    data = []
    for i in range(months - 1, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        month_total = sum(e.amount for e in expenses
                         if e.date.year == y and e.date.month == m)
        data.append(round(month_total, 2))
    return data


def _monthly_profit_sparkline(payments: list, expenses: list, today: date, months: int) -> list[float]:
    """Get monthly profit totals for the last N months."""
    rev = _monthly_sparkline(payments, today, months)
    exp = _monthly_expense_sparkline(expenses, today, months)
    return [round(r - e, 2) for r, e in zip(rev, exp)]


def _monthly_booking_sparkline(events: list, today: date, months: int) -> list[float]:
    """Get monthly event count for the last N months."""
    data = []
    for i in range(months - 1, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        count = len([e for e in events
                     if e.event_date and e.event_date.year == y and e.event_date.month == m])
        data.append(float(count))
    return data


def _quarter_range(today: date, offset: int = 0) -> tuple:
    """Get start, end, and label for a quarter offset from today."""
    cur_q = (today.month - 1) // 3 + 1
    cur_y = today.year

    # Go back offset quarters
    q = cur_q - offset
    y = cur_y
    while q <= 0:
        q += 4
        y -= 1

    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    start = date(y, start_month, 1)
    # End of quarter
    if end_month == 12:
        end = date(y, 12, 31)
    else:
        end = date(y, end_month + 1, 1) - timedelta(days=1)

    label = f"Q{q} {y}"
    return start, end, label


def _parse_created_month(created_at: str, target_date: date) -> bool:
    """Check if created_at falls in the same month as target_date."""
    try:
        dt = date.fromisoformat(created_at[:10])
        return dt.year == target_date.year and dt.month == target_date.month
    except (ValueError, TypeError):
        return False


def _fmt_money(value: float) -> str:
    """Quick Indian format for display."""
    if value >= 10000000:
        return f"₹{value / 10000000:.1f}Cr"
    if value >= 100000:
        return f"₹{value / 100000:.1f}L"
    if value >= 1000:
        return f"₹{value / 1000:.1f}K"
    return f"₹{value:.0f}"
