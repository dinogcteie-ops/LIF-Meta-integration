import csv
import io
from datetime import date

from openpyxl import Workbook

from app.services.reports import event_profits, monthly_history
from app.database import SheetDB


def _rows_events(db: SheetDB) -> list[list]:
    rows = [["ID", "Name", "Client", "Event Date", "Quoted Amount", "Status",
             "Income", "Expense", "Profit", "Notes"]]
    for ep in event_profits(db):
        ev = ep.event
        rows.append([
            ev.id, ev.name, ev.client_name or "", ev.event_date,
            ev.quoted_amount, ev.status.value,
            ep.income, ep.expense, ep.profit, ev.notes or "",
        ])
    return rows


def _rows_payments(db: SheetDB) -> list[list]:
    events = {e.id: e for e in db.list_events()}
    rows = [["ID", "Event", "Amount", "Payment Date", "Notes"]]
    for p in db.list_payments():
        rows.append([
            p.id,
            events[p.event_id].name if p.event_id in events else "",
            p.amount, p.payment_date, p.notes or "",
        ])
    return rows


def _rows_expenses(db: SheetDB) -> list[list]:
    events = {e.id: e for e in db.list_events()}
    cats   = {c.id: c for c in db.list_categories()}
    rows = [["ID", "Date", "Scope", "Event", "Category", "Amount",
             "Paid Amount", "Status", "Paid To", "Notes"]]
    for e in db.list_expenses():
        rows.append([
            e.id, e.date, e.scope.value,
            events[e.event_id].name if e.event_id in events else "",
            cats[e.category_id].name if e.category_id in cats else "",
            e.amount, e.paid_amount, e.payment_status.value,
            e.paid_to or "", e.notes or "",
        ])
    return rows


def _rows_summary(db: SheetDB) -> list[list]:
    rows = [["Period", "Income", "Expense", "Profit"]]
    for m in monthly_history(db, months=12):
        rows.append([m.period, m.income, m.expense, m.profit])
    return rows


def build_workbook(db: SheetDB) -> bytes:
    wb = Workbook()
    first_sheet = True
    for title, rows in [
        ("Events",          _rows_events(db)),
        ("Payments",        _rows_payments(db)),
        ("Expenses",        _rows_expenses(db)),
        ("Monthly Summary", _rows_summary(db)),
    ]:
        ws = wb.active if first_sheet else wb.create_sheet()
        ws.title = title
        first_sheet = False
        for row in rows:
            ws.append([_cell(v) for v in row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_csv(db: SheetDB, kind: str) -> str:
    rows = {
        "events":   _rows_events,
        "payments": _rows_payments,
        "expenses": _rows_expenses,
        "summary":  _rows_summary,
    }[kind](db)
    buf = io.StringIO()
    csv.writer(buf).writerows([[_cell(v) for v in row] for row in rows])
    return buf.getvalue()


def _cell(v):
    if isinstance(v, date):
        return v.isoformat()
    return v


def all_datasets(db: SheetDB) -> dict[str, list[list]]:
    """Used by Google Sheets sync — same row data, multiple tabs."""
    return {
        "Events":          _rows_events(db),
        "Payments":        _rows_payments(db),
        "Expenses":        _rows_expenses(db),
        "Monthly Summary": _rows_summary(db),
    }
