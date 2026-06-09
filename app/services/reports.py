from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.database import SheetDB
from app.domain import Event, EventPayment, Expense, ExpenseCategory, Lead
from app.enums import EventStatus, PaymentStatus


@dataclass
class EventProfit:
    event: Event
    income: float     # actual payments received
    expense: float    # actual expenses incurred

    @property
    def profit(self) -> float:
        """Realized profit = actual income received minus expenses."""
        return round(self.income - self.expense, 2)

    @property
    def expected_profit(self) -> float:
        """Projected profit = quoted amount minus expenses (assumes full collection)."""
        return round(self.event.quoted_amount - self.expense, 2)

    @property
    def pending_from_client(self) -> float:
        return round(max(0.0, self.event.quoted_amount - self.income), 2)

    @property
    def profit_pct(self) -> float:
        """Realized profit margin = profit / income received × 100."""
        if self.income <= 0:
            return 0.0
        return round(self.profit / self.income * 100, 1)

    @property
    def expected_profit_pct(self) -> float:
        """Projected margin = expected_profit / quoted_amount × 100."""
        if self.event.quoted_amount <= 0:
            return 0.0
        return round(self.expected_profit / self.event.quoted_amount * 100, 1)

    @property
    def collection_pct(self) -> float:
        """How much of the quoted amount has been collected."""
        if self.event.quoted_amount <= 0:
            return 0.0
        return round(self.income / self.event.quoted_amount * 100, 1)

    @property
    def expense_pct(self) -> float:
        if self.event.quoted_amount <= 0:
            return 0.0
        return round(self.expense / self.event.quoted_amount * 100, 1)


@dataclass
class BankSummary:
    total_income: float = 0.0
    total_paid_expense: float = 0.0
    outstanding_payable: float = 0.0
    total_pending_from_clients: float = 0.0
    booked_advance: float = 0.0        # portion of total_income from booked events
    booked_advance_count: int = 0      # number of booked events that have at least one payment

    @property
    def balance(self) -> float:
        return round(self.total_income - self.total_paid_expense, 2)


@dataclass
class MonthlyRow:
    period: str
    income: float = 0.0
    expense: float = 0.0
    projected: bool = False

    @property
    def profit(self) -> float:
        return round(self.income - self.expense, 2)


@dataclass
class ReportBundle:
    bank: BankSummary
    events: list[EventProfit] = field(default_factory=list)
    monthly: list[MonthlyRow] = field(default_factory=list)
    quarterly: list[MonthlyRow] = field(default_factory=list)


@dataclass
class ExpenseBreakdown:
    marketing:     list  # list of (category_name, total_amount)
    company_other: list
    founder:       list


def expense_breakdown(db: SheetDB) -> ExpenseBreakdown:
    """Return expenses grouped into Marketing / Other Company / Founder buckets."""
    cats  = {c.id: c for c in db.list_categories()}
    exps  = db.list_expenses()
    totals: dict[str, float] = {}
    scopes: dict[str, str]   = {}
    for e in exps:
        cat = cats.get(e.category_id)
        if not cat:
            continue
        key = cat.name
        totals[key] = round(totals.get(key, 0.0) + e.amount, 2)
        scopes[key]  = cat.scope.value
    marketing: list     = []
    company_other: list = []
    founder: list       = []
    for name, amt in sorted(totals.items(), key=lambda x: -x[1]):
        scope = scopes.get(name, "")
        if scope == "company" and name.lower() == "marketing":
            marketing.append((name, amt))
        elif scope == "company":
            company_other.append((name, amt))
        elif scope == "personal":
            founder.append((name, amt))
    return ExpenseBreakdown(marketing=marketing, company_other=company_other, founder=founder)


def _paid_total(expense: Expense) -> float:
    if expense.payment_status == PaymentStatus.paid:
        return expense.amount
    if expense.payment_status == PaymentStatus.partial:
        return expense.paid_amount or 0.0
    return 0.0


def bank_summary(db: SheetDB) -> BankSummary:
    summary = BankSummary()
    # Build set of event IDs that are in "booked" status
    booked_event_ids = {ev.id for ev in db.list_events() if ev.status == EventStatus.booked}
    booked_with_payments: set[int] = set()
    for p in db.list_payments():
        summary.total_income += p.amount
        if p.event_id in booked_event_ids:
            summary.booked_advance += p.amount
            booked_with_payments.add(p.event_id)
    summary.booked_advance_count = len(booked_with_payments)
    for e in db.list_expenses():
        paid = _paid_total(e)
        summary.total_paid_expense += paid
        if e.payment_status == PaymentStatus.pending:
            summary.outstanding_payable += e.amount
        elif e.payment_status == PaymentStatus.partial:
            summary.outstanding_payable += max(0.0, e.amount - paid)
    for ev in db.list_events_enriched():
        if ev.status == EventStatus.cancelled:   # Sprint 8 Bug A: exclude cancelled events
            continue
        income = sum(p.amount for p in ev.payments)
        summary.total_pending_from_clients += max(0.0, ev.quoted_amount - income)
    summary.total_income = round(summary.total_income, 2)
    summary.total_paid_expense = round(summary.total_paid_expense, 2)
    summary.outstanding_payable = round(summary.outstanding_payable, 2)
    summary.total_pending_from_clients = round(summary.total_pending_from_clients, 2)
    summary.booked_advance = round(summary.booked_advance, 2)
    return summary


def event_profits(db: SheetDB) -> list[EventProfit]:
    rows: list[EventProfit] = []
    for ev in db.list_events_enriched():
        income  = sum(p.amount for p in ev.payments)
        expense = sum(e.amount for e in ev.expenses)
        rows.append(EventProfit(event=ev, income=round(income, 2), expense=round(expense, 2)))
    rows.sort(key=lambda r: (r.event.event_date or date.min), reverse=True)
    return rows


def event_profit(db: SheetDB, event_id: int) -> EventProfit | None:
    ev = db.get_event(event_id)
    if ev is None:
        return None
    db.enrich_event(ev)
    income  = sum(p.amount for p in ev.payments)
    expense = sum(e.amount for e in ev.expenses)
    return EventProfit(event=ev, income=round(income, 2), expense=round(expense, 2))


def _period_key(d: date, quarterly: bool = False) -> str:
    if quarterly:
        q = (d.month - 1) // 3 + 1
        return f"{d.year}-Q{q}"
    return f"{d.year}-{d.month:02d}"


def _month_iter(start: date, count: int):
    y, m = start.year, start.month
    for _ in range(count):
        yield date(y, m, 1)
        m += 1
        if m > 12:
            m = 1
            y += 1


def monthly_history(db: SheetDB, months: int = 6, today: date | None = None) -> list[MonthlyRow]:
    today = today or date.today()
    y, m = today.year, today.month
    for _ in range(months - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    start = date(y, m, 1)

    bucket: dict[str, MonthlyRow] = {}
    for d in _month_iter(start, months):
        key = _period_key(d)
        bucket[key] = MonthlyRow(period=key)

    for p in db.list_payments():
        key = _period_key(p.payment_date)
        if key in bucket:
            bucket[key].income += p.amount
    for e in db.list_expenses():
        key = _period_key(e.date)
        if key in bucket:
            bucket[key].expense += e.amount

    return [bucket[_period_key(d)] for d in _month_iter(start, months)]


def project_next(rows: list[MonthlyRow], months: int = 3) -> list[MonthlyRow]:
    if not rows:
        return []
    avg_income  = sum(r.income for r in rows) / len(rows)
    avg_expense = sum(r.expense for r in rows) / len(rows)
    last_year, last_month = (int(x) for x in rows[-1].period.split("-"))
    out = []
    for _ in range(months):
        last_month += 1
        if last_month > 12:
            last_month = 1
            last_year += 1
        out.append(MonthlyRow(
            period=f"{last_year}-{last_month:02d}",
            income=round(avg_income, 2),
            expense=round(avg_expense, 2),
            projected=True,
        ))
    return out


def quarterly_history(db: SheetDB, quarters: int = 4, today: date | None = None) -> list[MonthlyRow]:
    today = today or date.today()
    cur_q = (today.month - 1) // 3 + 1
    keys: list[str] = []
    y, q = today.year, cur_q
    for _ in range(quarters):
        keys.append(f"{y}-Q{q}")
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    keys.reverse()
    bucket = {k: MonthlyRow(period=k) for k in keys}

    for p in db.list_payments():
        key = _period_key(p.payment_date, quarterly=True)
        if key in bucket:
            bucket[key].income += p.amount
    for e in db.list_expenses():
        key = _period_key(e.date, quarterly=True)
        if key in bucket:
            bucket[key].expense += e.amount

    return [bucket[k] for k in keys]


# ─── Phase 1: Cash Flow Command Center ──────────────────────────────────────


@dataclass
class ReceivableRow:
    """One row on the Receivables aging page."""
    event: Event
    income: float
    pending: float
    days_overdue: int          # negative if not yet due, 0 if due today
    bucket: str                # 'not_due', '0_30', '31_60', '60_plus'
    last_reminder_sent: object = None  # date or None
    reminder_notes: object = None      # str or None


@dataclass
class PayableRow:
    """One row on the Payables aging page."""
    expense: Expense
    pending: float             # amount - paid_amount
    days_overdue: int
    bucket: str
    payee_label: str           # paid_to or 'Unspecified'


@dataclass
class AgingTotals:
    """Pill-card counts for the four aging buckets."""
    not_due_count: int = 0
    not_due_amount: float = 0.0
    bucket_0_30_count: int = 0
    bucket_0_30_amount: float = 0.0
    bucket_31_60_count: int = 0
    bucket_31_60_amount: float = 0.0
    bucket_60_plus_count: int = 0
    bucket_60_plus_amount: float = 0.0

    @property
    def total_count(self) -> int:
        return (self.not_due_count + self.bucket_0_30_count
                + self.bucket_31_60_count + self.bucket_60_plus_count)

    @property
    def total_amount(self) -> float:
        return round(self.not_due_amount + self.bucket_0_30_amount
                     + self.bucket_31_60_amount + self.bucket_60_plus_amount, 2)

    @property
    def overdue_amount(self) -> float:
        """Total dollar amount that is past due (sum of three overdue buckets)."""
        return round(self.bucket_0_30_amount + self.bucket_31_60_amount
                     + self.bucket_60_plus_amount, 2)


def _bucket_for(days_overdue: int) -> str:
    if days_overdue < 0:
        return "not_due"
    if days_overdue <= 30:
        return "0_30"
    if days_overdue <= 60:
        return "31_60"
    return "60_plus"


def _accumulate(totals: AgingTotals, bucket: str, amount: float) -> None:
    if bucket == "not_due":
        totals.not_due_count += 1
        totals.not_due_amount += amount
    elif bucket == "0_30":
        totals.bucket_0_30_count += 1
        totals.bucket_0_30_amount += amount
    elif bucket == "31_60":
        totals.bucket_31_60_count += 1
        totals.bucket_31_60_amount += amount
    else:
        totals.bucket_60_plus_count += 1
        totals.bucket_60_plus_amount += amount


def receivables_aging(db: SheetDB, today: date | None = None,
                      grace_days: int = 0) -> tuple[list[ReceivableRow], AgingTotals]:
    """Build the receivables aging table + bucket totals.

    All events with a pending balance are included (booked, active, completed,
    cancelled). Events with no pending balance are excluded.
    Days overdue = today - (event_date + grace_days). If event_date is missing,
    the row is shown in 'not_due' bucket with days_overdue=-1.
    Cancelled events are included so their advance/balance is visible but they
    are shown in a separate section on the page (not counted in aging totals).
    """
    today = today or date.today()
    rows: list[ReceivableRow] = []
    totals = AgingTotals()
    for r in event_profits(db):
        ev = r.event
        if r.pending_from_client <= 0:
            continue
        if ev.event_date:
            due = ev.event_date + timedelta(days=grace_days)
            days_overdue = (today - due).days
        else:
            days_overdue = -1
        bucket = _bucket_for(days_overdue)
        # Cancelled events show in their own section; exclude from aging pill totals
        if ev.status != EventStatus.cancelled:
            _accumulate(totals, bucket, r.pending_from_client)
        rows.append(ReceivableRow(
            event=ev,
            income=r.income,
            pending=r.pending_from_client,
            days_overdue=days_overdue,
            bucket=bucket,
            last_reminder_sent=ev.last_reminder_sent,
            reminder_notes=ev.reminder_notes,
        ))
    # Sort: most overdue first, then by event date
    rows.sort(key=lambda x: (-x.days_overdue, x.event.event_date or date.min))
    # Round totals
    totals.not_due_amount = round(totals.not_due_amount, 2)
    totals.bucket_0_30_amount = round(totals.bucket_0_30_amount, 2)
    totals.bucket_31_60_amount = round(totals.bucket_31_60_amount, 2)
    totals.bucket_60_plus_amount = round(totals.bucket_60_plus_amount, 2)
    return rows, totals


def _expense_pending(e: Expense) -> float:
    """How much of this expense is still unpaid."""
    if e.payment_status == PaymentStatus.paid:
        return 0.0
    if e.payment_status == PaymentStatus.pending:
        return e.amount
    # partial
    return max(0.0, e.amount - (e.paid_amount or 0.0))


def payables_aging(db: SheetDB, today: date | None = None,
                   grace_days: int = 0) -> tuple[list[PayableRow], AgingTotals]:
    """Build the payables aging table + bucket totals.

    Includes any expense whose payment_status is pending or partial.
    Days overdue = today - (expense.date + grace_days).
    """
    today = today or date.today()
    rows: list[PayableRow] = []
    totals = AgingTotals()
    for e in db.list_expenses():
        pending = _expense_pending(e)
        if pending <= 0:
            continue
        if e.date:
            due = e.date + timedelta(days=grace_days)
            days_overdue = (today - due).days
        else:
            days_overdue = -1
        bucket = _bucket_for(days_overdue)
        _accumulate(totals, bucket, pending)
        rows.append(PayableRow(
            expense=e,
            pending=round(pending, 2),
            days_overdue=days_overdue,
            bucket=bucket,
            payee_label=(e.paid_to or "Unspecified"),
        ))
    rows.sort(key=lambda x: -x.days_overdue)
    totals.not_due_amount = round(totals.not_due_amount, 2)
    totals.bucket_0_30_amount = round(totals.bucket_0_30_amount, 2)
    totals.bucket_31_60_amount = round(totals.bucket_31_60_amount, 2)
    totals.bucket_60_plus_amount = round(totals.bucket_60_plus_amount, 2)
    return rows, totals


@dataclass
class CashFlowAlert:
    severity: str   # 'critical', 'warning', 'info'
    icon: str       # bootstrap icon name (without 'bi-' prefix)
    message: str
    link: str       # URL to open the underlying record


def cash_flow_alerts(db: SheetDB, today: date | None = None,
                     grace_days: int = 0,
                     overdue_threshold_days: int = 30,
                     upcoming_shoot_days: int = 14) -> list[CashFlowAlert]:
    """Build the dashboard 'Attention' banner items (Phase 1.4).

    Surfaces:
      - Clients overdue 30+ days
      - Payables overdue 30+ days
      - Booked events within 14 days that have no advance recorded
    Returns up to 3 most urgent items.
    """
    today = today or date.today()
    alerts: list[CashFlowAlert] = []

    # 1. Critically overdue clients
    rec_rows, _ = receivables_aging(db, today, grace_days)
    for r in rec_rows:
        if r.days_overdue >= overdue_threshold_days:
            client = r.event.client_name or r.event.name
            alerts.append(CashFlowAlert(
                severity="critical",
                icon="exclamation-octagon-fill",
                message=f"{client} is {r.days_overdue} days overdue ({r.pending:,.0f} pending)",
                link=f"/events/{r.event.id}",
            ))

    # 2. Critically overdue payables
    pay_rows, _ = payables_aging(db, today, grace_days)
    for p in pay_rows:
        if p.days_overdue >= overdue_threshold_days:
            alerts.append(CashFlowAlert(
                severity="warning",
                icon="cash-stack",
                message=f"{p.payee_label} unpaid {p.days_overdue} days ({p.pending:,.0f})",
                link=f"/expenses/{p.expense.id}/edit",
            ))

    # 3. Booked shoots within N days with no advance recorded
    for ev in db.list_events():
        if ev.status != EventStatus.booked:
            continue
        if not ev.event_date:
            continue
        days_until = (ev.event_date - today).days
        if 0 <= days_until <= upcoming_shoot_days:
            received = sum(p.amount for p in db.list_payments(event_id=ev.id))
            if received <= 0:
                alerts.append(CashFlowAlert(
                    severity="info",
                    icon="calendar-event",
                    message=(f"{ev.name} is in {days_until} day"
                             f"{'s' if days_until != 1 else ''} — no advance yet"),
                    link=f"/events/{ev.id}",
                ))

    # 4. Categories that have already exceeded their monthly budget (Sprint 6)
    try:
        over = [r for r in budget_vs_actual(db, today.year, today.month) if r.over_budget]
        for r in over:
            over_by = r.actual - r.budget
            alerts.append(CashFlowAlert(
                severity="warning",
                icon="exclamation-diamond",
                message=(f"'{r.category.name}' is {over_by:,.0f} over budget "
                         f"({r.pct_used}% of {r.budget:,.0f})"),
                link="/reports",
            ))
    except Exception:
        # Budgets are advisory; never block the alerts banner on a budget error
        pass

    # Order: critical → warning → info, then up to 3
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a.severity, 99))
    return alerts[:3]


# ─── Phase 2: Directory analytics ───────────────────────────────────────────


@dataclass
class ClientStat:
    """Aggregated revenue statistics for one client."""
    name: str
    client_id: Optional[int]
    event_count: int
    total_quoted: float
    total_received: float

    @property
    def total_pending(self) -> float:
        return round(max(0.0, self.total_quoted - self.total_received), 2)


@dataclass
class PayeeStat:
    """Aggregated spend statistics for one payee/vendor."""
    name: str
    payee_id: Optional[int]
    payee_type: str
    expense_count: int
    total_spent: float


def top_clients(event_rows: list[EventProfit], clients_map: dict, n: int = 8) -> list[ClientStat]:
    """Return top N clients by total quoted revenue.

    Accepts already-loaded event_rows to avoid a duplicate DB round-trip.
    clients_map: {client_id: Client} — used to resolve names for linked records.
    """
    buckets: dict[str, dict] = {}
    for r in event_rows:
        ev = r.event
        if ev.client_id and ev.client_id in clients_map:
            key = f"id:{ev.client_id}"
            label = clients_map[ev.client_id].name
            cid: Optional[int] = ev.client_id
        elif ev.client_name:
            key = f"name:{ev.client_name.lower()}"
            label = ev.client_name
            cid = None
        else:
            continue
        if key not in buckets:
            buckets[key] = {"name": label, "client_id": cid,
                            "event_count": 0, "total_quoted": 0.0, "total_received": 0.0}
        buckets[key]["event_count"] += 1
        buckets[key]["total_quoted"] += ev.quoted_amount
        buckets[key]["total_received"] += r.income

    stats = [
        ClientStat(
            name=v["name"], client_id=v["client_id"],
            event_count=v["event_count"],
            total_quoted=round(v["total_quoted"], 2),
            total_received=round(v["total_received"], 2),
        )
        for v in buckets.values()
    ]
    stats.sort(key=lambda x: -x.total_quoted)
    return stats[:n]


def top_payees(expenses: list, payees_map: dict, n: int = 8) -> list[PayeeStat]:
    """Return top N payees by total expense amount.

    Accepts already-loaded expenses + payees_map to avoid duplicate DB calls.
    payees_map: {payee_id: Payee}
    """
    buckets: dict[str, dict] = {}
    for e in expenses:
        if e.payee_id and e.payee_id in payees_map:
            p = payees_map[e.payee_id]
            key = f"id:{e.payee_id}"
            label = p.name
            pid: Optional[int] = e.payee_id
            ptype = p.payee_type
        elif e.paid_to:
            key = f"name:{e.paid_to.lower()}"
            label = e.paid_to
            pid = None
            ptype = "vendor"
        else:
            continue
        if key not in buckets:
            buckets[key] = {"name": label, "payee_id": pid, "payee_type": ptype,
                            "expense_count": 0, "total_spent": 0.0}
        buckets[key]["expense_count"] += 1
        buckets[key]["total_spent"] += e.amount

    stats = [
        PayeeStat(
            name=v["name"], payee_id=v["payee_id"], payee_type=v["payee_type"],
            expense_count=v["expense_count"],
            total_spent=round(v["total_spent"], 2),
        )
        for v in buckets.values()
        if v["total_spent"] > 0
    ]
    stats.sort(key=lambda x: -x.total_spent)
    return stats[:n]


# ─── Phase 4: Lead Pipeline ──────────────────────────────────────────────────


@dataclass
class LeadFunnel:
    """Aggregated counts for the lead funnel dashboard widget."""
    new_count:          int   = 0
    quoted_count:       int   = 0
    won_count:          int   = 0
    lost_count:         int   = 0
    cold_count:         int   = 0
    total_pipeline_value: float = 0.0  # new + quoted quoted_amount
    total_won_value:    float = 0.0

    @property
    def total_active(self) -> int:
        return self.new_count + self.quoted_count + self.won_count + self.lost_count

    @property
    def total_all(self) -> int:
        """Every lead in the (filtered) set, including cold."""
        return self.total_active + self.cold_count

    @property
    def conversion_rate(self) -> float:
        denom = self.new_count + self.quoted_count + self.won_count + self.lost_count
        if not denom:
            return 0.0
        return round(self.won_count / denom * 100, 1)

    @property
    def won_rate(self) -> float:
        """Alias of conversion_rate — share of decided/active leads that were won."""
        return self.conversion_rate

    @property
    def lost_rate(self) -> float:
        denom = self.new_count + self.quoted_count + self.won_count + self.lost_count
        if not denom:
            return 0.0
        return round(self.lost_count / denom * 100, 1)


@dataclass
class LostReasonStat:
    """One row in the lost-lead reason breakdown."""
    reason: str
    count:  int
    value:  float   # total quoted value that walked away under this reason

    def pct_of(self, total: int) -> float:
        return round(self.count / total * 100, 1) if total else 0.0


@dataclass
class LostReasonBreakdown:
    """Why we lose leads — aggregated for the dashboard summary widget."""
    rows:        list[LostReasonStat] = field(default_factory=list)
    total_lost:  int   = 0
    total_value: float = 0.0

    @property
    def has_data(self) -> bool:
        return self.total_lost > 0

    @property
    def labels(self) -> list[str]:
        return [r.reason for r in self.rows]

    @property
    def data(self) -> list[int]:
        return [r.count for r in self.rows]

    @property
    def top_reason(self) -> "LostReasonStat | None":
        return self.rows[0] if self.rows else None


def _lead_created_date(lead: Lead) -> "date | None":
    """Parse the enquiry date from a lead's created_at ISO string."""
    raw = (lead.created_at or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def filter_leads(leads: list[Lead], source: str | None = None,
                 start: "date | None" = None,
                 end: "date | None" = None) -> list[Lead]:
    """Leads filtered by source and by enquiry date (created_at).

    When a date range is set, leads with no parseable created_at are excluded.
    A ``source`` of None / "" / "all" means every source.
    """
    out: list[Lead] = []
    for lead in leads:
        if source and source != "all" and (lead.source or "") != source:
            continue
        if start or end:
            d = _lead_created_date(lead)
            if d is None:
                continue
            if start and d < start:
                continue
            if end and d > end:
                continue
        out.append(lead)
    return out


def filter_lost_leads(leads: list[Lead], source: str | None = None,
                      start: "date | None" = None,
                      end: "date | None" = None) -> list[Lead]:
    """Lost leads only, filtered by source and enquiry date — see ``filter_leads``."""
    return [l for l in filter_leads(leads, source, start, end) if l.status == "lost"]


def lost_reason_breakdown(leads: list[Lead]) -> LostReasonBreakdown:
    """Group lost leads by their standardized rejection reason.

    Counts and total quoted value are bucketed per reason; leads marked lost
    with no reason recorded fall into 'Unspecified'. Rows are sorted by count
    descending so the biggest leak surfaces first.
    """
    buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "value": 0.0})
    for lead in leads:
        if lead.status != "lost":
            continue
        reason = (lead.rejection_reason or "").strip() or "Unspecified"
        buckets[reason]["count"] += 1
        buckets[reason]["value"] += lead.quoted_amount or 0.0
    rows = [
        LostReasonStat(reason=r, count=d["count"], value=round(d["value"], 2))
        for r, d in sorted(buckets.items(), key=lambda x: (-x[1]["count"], x[0]))
    ]
    return LostReasonBreakdown(
        rows=rows,
        total_lost=sum(r.count for r in rows),
        total_value=round(sum(r.value for r in rows), 2),
    )


def lead_funnel(leads: list[Lead]) -> LeadFunnel:
    """Compute funnel counts from a pre-loaded list of leads."""
    funnel = LeadFunnel()
    for lead in leads:
        if lead.status == "new":
            funnel.new_count += 1
            funnel.total_pipeline_value += lead.quoted_amount
        elif lead.status == "quoted":
            funnel.quoted_count += 1
            funnel.total_pipeline_value += lead.quoted_amount
        elif lead.status == "won":
            funnel.won_count += 1
            funnel.total_won_value += lead.quoted_amount
        elif lead.status == "lost":
            funnel.lost_count += 1
        elif lead.status == "cold":
            funnel.cold_count += 1
    funnel.total_pipeline_value = round(funnel.total_pipeline_value, 2)
    funnel.total_won_value = round(funnel.total_won_value, 2)
    return funnel


# ─── Phase 5.3: Budgets (Sprint 6) ───────────────────────────────────────────


@dataclass
class BudgetRow:
    """Per-category monthly budget vs actual."""
    category: ExpenseCategory
    budget:   float
    actual:   float

    @property
    def remaining(self) -> float:
        return round(self.budget - self.actual, 2)

    @property
    def pct_used(self) -> float:
        if self.budget <= 0:
            return 0.0
        return round(self.actual / self.budget * 100, 1)

    @property
    def over_budget(self) -> bool:
        return self.budget > 0 and self.actual > self.budget

    @property
    def has_budget(self) -> bool:
        return self.budget > 0


def budget_vs_actual(db: SheetDB, year: int, month: int) -> list[BudgetRow]:
    """Per-category actual spending vs monthly budget for a target month.

    Returns rows for any active category that has either a budget set
    or actual spend in the target month. Sorted: over-budget first,
    then by % of budget used descending.
    """
    cats = db.list_categories(active_only=True)
    expenses = [
        e for e in db.list_expenses()
        if e.date.year == year and e.date.month == month
    ]
    actuals_by_cat: dict[int, float] = {}
    for e in expenses:
        actuals_by_cat[e.category_id] = actuals_by_cat.get(e.category_id, 0.0) + e.amount

    rows: list[BudgetRow] = []
    for c in cats:
        actual = round(actuals_by_cat.get(c.id, 0.0), 2)
        if c.monthly_budget > 0 or actual > 0:
            rows.append(BudgetRow(category=c, budget=c.monthly_budget, actual=actual))

    # Sort: over-budget first, then by % used desc
    rows.sort(key=lambda r: (
        0 if r.over_budget else (1 if r.has_budget else 2),
        -r.pct_used,
    ))
    return rows


def budget_alert_count(db: SheetDB, year: int, month: int) -> int:
    """Number of categories currently over their monthly budget."""
    return sum(1 for r in budget_vs_actual(db, year, month) if r.over_budget)


def source_conversion(leads: list[Lead]) -> list[dict]:
    """Return per-source conversion stats, sorted by total leads desc."""
    buckets: dict[str, dict] = defaultdict(lambda: {"total": 0, "won": 0, "value": 0.0})
    for lead in leads:
        src = lead.source or "Unknown"
        buckets[src]["total"] += 1
        if lead.status == "won":
            buckets[src]["won"] += 1
            buckets[src]["value"] += lead.quoted_amount
    result = []
    for src, d in sorted(buckets.items(), key=lambda x: -x[1]["total"]):
        pct = round(d["won"] / d["total"] * 100, 0) if d["total"] else 0.0
        result.append({
            "source": src,
            "total": d["total"],
            "won": d["won"],
            "pct": pct,
            "value": round(d["value"], 2),
        })
    return result
