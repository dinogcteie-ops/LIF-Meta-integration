"""Google Sheets as the application database.

DB tabs (prefixed db_) are separate from the export/report tabs written by sheets.py:
  db_categories  — ExpenseCategory rows
  db_events      — Event rows
  db_payments    — EventPayment rows
  db_expenses    — Expense rows
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import gspread
from gspread.exceptions import APIError

from app.config import get_settings
from app.enums import CategoryScope, EventStatus, PaymentStatus
from app.services.google_auth import load_google_credentials

# ─── Tab names ───────────────────────────────────────────────────────────────

_T_CATS      = "db_categories"
_T_EVENTS    = "db_events"
_T_PAYMENTS  = "db_payments"
_T_EXPENSES  = "db_expenses"
_T_CLIENTS   = "db_clients"
_T_PAYEES    = "db_payees"
_T_SETTINGS  = "db_settings"   # Phase 3 — studio config key-value store
_T_AUDIT     = "db_audit"      # Phase 3 — operation audit log
_T_LEADS     = "db_leads"      # Phase 4 — pre-booking lead pipeline

_HEADERS: dict[str, list[str]] = {
    _T_CATS:     ["id", "name", "scope", "is_active", "created_at", "monthly_budget"],
    _T_EVENTS:   ["id", "name", "client_name", "event_date", "quoted_amount", "status", "notes", "created_at",
                  "event_type", "location", "referral_source",
                  "payment_due_dates", "last_reminder_sent", "reminder_notes", "client_id",
                  "delivery_status"],
    _T_PAYMENTS: ["id", "event_id", "amount", "payment_date", "notes", "created_at"],
    _T_EXPENSES: ["id", "date", "event_id", "category_id", "scope", "payment_status",
                  "amount", "paid_amount", "paid_to", "notes", "created_at", "payee_id",
                  "is_recurring", "recurring_day", "payment_type"],
    _T_CLIENTS:  ["id", "name", "phone", "email", "address", "notes", "created_at"],
    _T_PAYEES:   ["id", "name", "payee_type", "phone", "email", "notes", "created_at"],
    _T_SETTINGS: ["id", "key", "value", "updated_at"],
    _T_AUDIT:    ["id", "timestamp", "entity_type", "entity_id", "action", "summary"],
    _T_LEADS:    ["id", "client_name", "contact", "event_type", "tentative_date",
                  "source", "status", "quoted_amount", "notes", "created_at", "client_id",
                  "num_events", "revised_quote", "follow_ups", "rejection_reason",
                  "meta_campaign", "referral_name", "followup_status", "followup_date"],
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ExpenseCategory:
    id: int
    name: str
    scope: CategoryScope
    is_active: bool = True
    created_at: str = ""
    monthly_budget: float = 0.0   # Phase 5.3 — Sprint 6


@dataclass
class Event:
    id: int
    name: str
    client_name: Optional[str] = None
    event_date: Optional[date] = None
    quoted_amount: float = 0.0
    status: EventStatus = EventStatus.active
    notes: Optional[str] = None
    created_at: str = ""
    event_type: Optional[str] = None
    location: Optional[str] = None
    referral_source: Optional[str] = None
    # Phase 1 cash-flow additions:
    payment_due_dates: Optional[str] = None   # JSON list of ISO dates, e.g. '["2025-12-01","2026-01-15"]'
    last_reminder_sent: Optional[date] = None
    reminder_notes: Optional[str] = None
    # Phase 2 directory FK:
    client_id: Optional[int] = None           # → db_clients; None = free-text client_name only
    # Phase 4 delivery workflow:
    delivery_status: Optional[str] = None     # shooting_done | editing | review | delivered
    payments: list[EventPayment] = field(default_factory=list, compare=False, repr=False)
    expenses: list[Expense] = field(default_factory=list, compare=False, repr=False)


@dataclass
class EventPayment:
    id: int
    event_id: int
    amount: float
    payment_date: date
    notes: Optional[str] = None
    created_at: str = ""
    event: Optional[Event] = field(default=None, compare=False, repr=False)


@dataclass
class Expense:
    id: int
    date: date
    category_id: int
    scope: CategoryScope
    payment_status: PaymentStatus
    amount: float
    event_id: Optional[int] = None
    paid_amount: float = 0.0
    paid_to: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""
    payee_id: Optional[int] = None          # Phase 2 FK → db_payees
    is_recurring: bool = False              # Phase 3 — acts as a monthly template
    recurring_day: Optional[int] = None    # Phase 3 — day-of-month for generation (1-31)
    payment_type: Optional[str] = None     # Sprint 7 — Cash/Credit/UPI/Current A/C/Savings A/C
    category: Optional[ExpenseCategory] = field(default=None, compare=False, repr=False)
    event: Optional[Event] = field(default=None, compare=False, repr=False)


# ─── Phase 2: Directory dataclasses ─────────────────────────────────────────

@dataclass
class Client:
    """A repeat client / contact associated with one or more events."""
    id: int
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""


@dataclass
class Payee:
    """A freelancer, vendor, or other party that the studio pays."""
    id: int
    name: str
    payee_type: str = "freelancer"   # freelancer | vendor | other
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = ""


@dataclass
class AuditEntry:
    """One row in the audit log (Phase 3)."""
    id: int
    timestamp: str
    entity_type: str    # event | payment | expense | client | payee
    entity_id: int
    action: str         # create | update | delete
    summary: str


@dataclass
class Lead:
    """A pre-booking enquiry / lead (Phase 4)."""
    id: int
    client_name: str
    contact: str = ""
    event_type: str = ""
    tentative_date: Optional[date] = None
    source: str = ""
    status: str = "new"      # new | quoted | won | lost | cold
    quoted_amount: float = 0.0
    notes: str = ""
    created_at: str = ""
    client_id: Optional[int] = None
    num_events: int = 0
    revised_quote: float = 0.0
    follow_ups: str = ""
    rejection_reason: str = ""
    meta_campaign: bool = False
    referral_name: str = ""
    followup_status: str = "pending"        # Sprint 7 — pending | scheduled | done
    followup_date: Optional[date] = None    # Sprint 7 — next follow-up date


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip())
    except (ValueError, TypeError):
        return None


def _parse_float(s) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _parse_int(s) -> Optional[int]:
    try:
        v = int(s)
        return v if v else None
    except (ValueError, TypeError):
        return None


def _parse_bool(s) -> bool:
    if isinstance(s, bool):
        return s
    return str(s).upper() in ("TRUE", "1", "YES")


def _now() -> str:
    return datetime.utcnow().isoformat()


# ─── Spreadsheet cache ───────────────────────────────────────────────────────

_sheet_cache: Optional[gspread.Spreadsheet] = None

# Short-lived in-memory cache of per-tab row data. The Google Sheets API allows
# only ~60 reads/min/user, and a single dashboard load fans out into dozens of
# tab reads — without this, bursty navigation trips the quota and surfaces as
# HTTP 500s. Entries live for _CACHE_TTL seconds and are dropped wholesale on
# any write (see _wrap_write_methods), so data stays fresh after edits.
_CACHE_TTL = 30.0  # seconds
_records_cache: dict[str, tuple[float, list[dict]]] = {}


def _invalidate_all() -> None:
    """Drop every cached read so the next read re-fetches from Sheets."""
    _records_cache.clear()


def _is_transient(exc: Exception) -> bool:
    """True for Sheets API errors worth retrying (rate-limit / transient 5xx)."""
    if isinstance(exc, APIError):
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return code in (429, 500, 502, 503, 504)
    return False


def _with_retry(fn, *, tries: int = 4, base_delay: float = 0.6):
    """Call fn(), retrying with exponential backoff on transient Sheets errors.
    Non-transient errors raise immediately; the last error re-raises after all
    attempts are exhausted."""
    for attempt in range(tries):
        try:
            return fn()
        except Exception as exc:
            if not _is_transient(exc) or attempt == tries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))


_WRITE_METHODS = ("append_row", "append_rows", "update",
                  "update_cell", "batch_update", "delete_rows")


def _wrap_write_methods(ws: gspread.Worksheet) -> None:
    """Shadow a worksheet's write methods so any mutation invalidates the read
    cache. Idempotent — safe to call repeatedly on the same worksheet."""
    if getattr(ws, "_lif_wrapped", False):
        return
    for name in _WRITE_METHODS:
        orig = getattr(ws, name, None)
        if orig is None:
            continue

        def make(orig):
            def wrapper(*args, **kwargs):
                result = orig(*args, **kwargs)
                _invalidate_all()
                return result
            return wrapper

        setattr(ws, name, make(orig))
    ws._lif_wrapped = True


def _open_spreadsheet() -> gspread.Spreadsheet:
    global _sheet_cache
    if _sheet_cache is None:
        creds = load_google_credentials()
        client = gspread.authorize(creds)
        _sheet_cache = client.open_by_key(get_settings().google_sheet_id)
    return _sheet_cache


def _ensure_tab(sh: gspread.Spreadsheet, name: str) -> gspread.Worksheet:
    titles = {ws.title for ws in sh.worksheets()}
    if name not in titles:
        ws = sh.add_worksheet(title=name, rows=500, cols=len(_HEADERS[name]) + 2)
        ws.update([_HEADERS[name]], "A1")
        return ws
    return sh.worksheet(name)


def _next_id(records: list[dict]) -> int:
    if not records:
        return 1
    ids = [int(r["id"]) for r in records if r.get("id")]
    return (max(ids) + 1) if ids else 1


def _read_records(ws: gspread.Worksheet, tab_name: str) -> list[dict]:
    """Read all rows for a tab, supplying expected_headers to avoid
    duplicate-empty-header errors from extra blank columns. Transient API
    errors (429/5xx) are retried; only header/parse problems fall through to
    the plainer read paths."""
    try:
        return _with_retry(
            lambda: ws.get_all_records(expected_headers=_HEADERS[tab_name]))
    except APIError:
        raise  # real (non-transient) API error — surface it, don't burn quota
    except Exception:
        # Header/parse problem — read without expected_headers (may have missing cols)
        try:
            return _with_retry(ws.get_all_records)
        except APIError:
            raise
        except Exception:
            # Last resort: manually parse from get_all_values
            all_vals = _with_retry(ws.get_all_values)
            if len(all_vals) < 2:
                return []
            headers = all_vals[0]
            return [
                {headers[i]: (row[i] if i < len(row) else "")
                 for i in range(len(headers)) if headers[i]}
                for row in all_vals[1:]
            ]


def _get_records(ws: gspread.Worksheet, tab_name: str) -> list[dict]:
    """Cached read of a tab's rows. Serves from the in-memory cache while fresh
    (see _CACHE_TTL); otherwise re-reads from Sheets and stores the result."""
    cached = _records_cache.get(tab_name)
    if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL:
        return cached[1]
    records = _read_records(ws, tab_name)
    _records_cache[tab_name] = (time.monotonic(), records)
    return records


def _find_row(ws: gspread.Worksheet, record_id: int) -> int:
    """Return 1-indexed sheet row (row 1 = headers). -1 if not found."""
    all_vals = _with_retry(ws.get_all_values)
    for i, row in enumerate(all_vals[1:], start=2):
        if row and str(row[0]) == str(record_id):
            return i
    return -1


def _sync_headers(ws: gspread.Worksheet, tab_name: str) -> None:
    """Non-destructively append any missing header columns to an existing sheet."""
    expected = _HEADERS[tab_name]
    existing = ws.row_values(1)
    missing = [h for h in expected if h not in existing]
    if not missing:
        return
    # Resize sheet if needed to fit new columns
    needed_cols = len(existing) + len(missing)
    if ws.col_count < needed_cols:
        ws.resize(cols=needed_cols + 2)
    start_col = len(existing) + 1
    for i, header in enumerate(missing):
        col_letter = gspread.utils.rowcol_to_a1(1, start_col + i)
        ws.update([[header]], col_letter)


# ─── SheetDB ─────────────────────────────────────────────────────────────────

class SheetDB:
    def __init__(self):
        sh = _open_spreadsheet()
        self._cats     = _ensure_tab(sh, _T_CATS)
        self._events   = _ensure_tab(sh, _T_EVENTS)
        self._payments = _ensure_tab(sh, _T_PAYMENTS)
        self._expenses = _ensure_tab(sh, _T_EXPENSES)
        self._clients  = _ensure_tab(sh, _T_CLIENTS)    # Phase 2
        self._payees   = _ensure_tab(sh, _T_PAYEES)     # Phase 2
        self._settings = _ensure_tab(sh, _T_SETTINGS)   # Phase 3
        self._audit    = _ensure_tab(sh, _T_AUDIT)      # Phase 3
        self._leads    = _ensure_tab(sh, _T_LEADS)      # Phase 4
        # Ensure new columns are present on existing sheets (non-destructive).
        # Each call is independent so one failure doesn't block the others.
        for ws, tab in [
            (self._events, _T_EVENTS),
            (self._expenses, _T_EXPENSES),
            (self._cats, _T_CATS),
            (self._leads, _T_LEADS),
        ]:
            try:
                _sync_headers(ws, tab)
            except Exception as exc:
                logging.warning("Could not sync headers for %s: %s", tab, exc)

        # Route all writes through cache-invalidating wrappers so edits are
        # reflected on the next read despite the read cache.
        for ws in (self._cats, self._events, self._payments, self._expenses,
                   self._clients, self._payees, self._settings, self._audit,
                   self._leads):
            _wrap_write_methods(ws)

    # ── Categories ────────────────────────────────────────────────────────────

    def list_categories(self, active_only: bool = False) -> list[ExpenseCategory]:
        cats = [
            ExpenseCategory(
                id=int(r["id"]),
                name=r["name"],
                scope=CategoryScope(r["scope"]),
                is_active=_parse_bool(r.get("is_active", "TRUE")),
                created_at=r.get("created_at", ""),
                monthly_budget=_parse_float(r.get("monthly_budget", 0)),
            )
            for r in _get_records(self._cats, _T_CATS)
            if r.get("id")
        ]
        if active_only:
            cats = [c for c in cats if c.is_active]
        return sorted(cats, key=lambda c: (c.scope, c.name))

    def get_category(self, cat_id: int) -> Optional[ExpenseCategory]:
        for c in self.list_categories():
            if c.id == cat_id:
                return c
        return None

    def create_category(self, name: str, scope: str,
                        monthly_budget: float = 0.0) -> ExpenseCategory:
        recs = _get_records(self._cats, _T_CATS)
        nid = _next_id(recs)
        now = _now()
        self._cats.append_row([nid, name, scope, "TRUE", now, monthly_budget])
        return ExpenseCategory(id=nid, name=name, scope=CategoryScope(scope),
                               is_active=True, created_at=now,
                               monthly_budget=monthly_budget)

    def update_category(self, cat_id: int, name: str, is_active: bool,
                        monthly_budget: Optional[float] = None) -> Optional[ExpenseCategory]:
        cat = self.get_category(cat_id)
        if cat is None:
            return None
        row = _find_row(self._cats, cat_id)
        if row == -1:
            return None
        if monthly_budget is None:
            monthly_budget = cat.monthly_budget
        self._cats.update([[cat_id, name, cat.scope.value,
                            "TRUE" if is_active else "FALSE",
                            cat.created_at, monthly_budget]], f"A{row}")
        cat.name = name
        cat.is_active = is_active
        cat.monthly_budget = monthly_budget
        return cat

    def seed_if_empty(self):
        self.seed_settings_if_empty()
        if self.list_categories():
            return
        event_cats    = ["Photographer", "Editor", "Videographer", "Album", "Food",
                         "Travel", "Hard Disk", "Reels Creator", "Miscellaneous", "Commission"]
        company_cats  = ["Marketing", "Travel", "Equipment", "Software", "Office"]
        personal_cats = ["Founder expenses"]
        rows = []
        nid, now = 1, _now()
        for name in event_cats:
            rows.append([nid, name, "event", "TRUE", now, 0]); nid += 1
        for name in company_cats:
            rows.append([nid, name, "company", "TRUE", now, 0]); nid += 1
        for name in personal_cats:
            rows.append([nid, name, "personal", "TRUE", now, 0]); nid += 1
        self._cats.append_rows(rows)

    # ── Events ────────────────────────────────────────────────────────────────

    def list_events(self) -> list[Event]:
        return [
            Event(
                id=int(r["id"]),
                name=r["name"],
                client_name=r.get("client_name") or None,
                event_date=_parse_date(r.get("event_date")),
                quoted_amount=_parse_float(r.get("quoted_amount", 0)),
                status=EventStatus(r.get("status", "active")),
                notes=r.get("notes") or None,
                created_at=r.get("created_at", ""),
                event_type=r.get("event_type") or None,
                location=r.get("location") or None,
                referral_source=r.get("referral_source") or None,
                payment_due_dates=r.get("payment_due_dates") or None,
                last_reminder_sent=_parse_date(r.get("last_reminder_sent")),
                reminder_notes=r.get("reminder_notes") or None,
                client_id=_parse_int(r.get("client_id", "")),
                delivery_status=r.get("delivery_status") or None,
            )
            for r in _get_records(self._events, _T_EVENTS)
            if r.get("id")
        ]

    def get_event(self, event_id: int) -> Optional[Event]:
        for e in self.list_events():
            if e.id == event_id:
                return e
        return None

    def create_event(self, name: str, client_name: Optional[str] = None,
                     event_date: Optional[date] = None, quoted_amount: float = 0.0,
                     status: str = "active", notes: Optional[str] = None,
                     event_type: Optional[str] = None, location: Optional[str] = None,
                     referral_source: Optional[str] = None,
                     payment_due_dates: Optional[str] = None,
                     last_reminder_sent: Optional[date] = None,
                     reminder_notes: Optional[str] = None,
                     client_id: Optional[int] = None,
                     delivery_status: Optional[str] = None) -> Event:
        nid = _next_id(_get_records(self._events, _T_EVENTS))
        now = _now()
        self._events.append_row([
            nid, name, client_name or "",
            event_date.isoformat() if event_date else "",
            quoted_amount, status, notes or "", now,
            event_type or "", location or "", referral_source or "",
            payment_due_dates or "",
            last_reminder_sent.isoformat() if last_reminder_sent else "",
            reminder_notes or "",
            client_id or "",
            delivery_status or "",
        ])
        self.log_audit("event", nid, "create", f"Created event '{name}'")
        return Event(id=nid, name=name, client_name=client_name, event_date=event_date,
                     quoted_amount=quoted_amount, status=EventStatus(status),
                     notes=notes, created_at=now,
                     event_type=event_type, location=location, referral_source=referral_source,
                     payment_due_dates=payment_due_dates,
                     last_reminder_sent=last_reminder_sent,
                     reminder_notes=reminder_notes,
                     client_id=client_id,
                     delivery_status=delivery_status)

    def update_event(self, event_id: int, name: str, client_name: Optional[str] = None,
                     event_date: Optional[date] = None, quoted_amount: float = 0.0,
                     status: str = "active", notes: Optional[str] = None,
                     event_type: Optional[str] = None, location: Optional[str] = None,
                     referral_source: Optional[str] = None,
                     payment_due_dates: Optional[str] = None,
                     last_reminder_sent: Optional[date] = None,
                     reminder_notes: Optional[str] = None,
                     client_id: Optional[int] = None,
                     delivery_status: Optional[str] = None) -> Optional[Event]:
        ev = self.get_event(event_id)
        if ev is None:
            return None
        # Preserve existing fields if caller passes None
        if payment_due_dates is None:
            payment_due_dates = ev.payment_due_dates
        if last_reminder_sent is None:
            last_reminder_sent = ev.last_reminder_sent
        if reminder_notes is None:
            reminder_notes = ev.reminder_notes
        if client_id is None:
            client_id = ev.client_id
        # delivery_status: None means "caller didn't provide one" → preserve existing
        # Use empty string sentinel to explicitly clear it
        if delivery_status is None:
            delivery_status = ev.delivery_status
        row = _find_row(self._events, event_id)
        self._events.update([[
            event_id, name, client_name or "",
            event_date.isoformat() if event_date else "",
            quoted_amount, status, notes or "", ev.created_at,
            event_type or "", location or "", referral_source or "",
            payment_due_dates or "",
            last_reminder_sent.isoformat() if last_reminder_sent else "",
            reminder_notes or "",
            client_id or "",
            delivery_status or "",
        ]], f"A{row}")
        ev.name = name; ev.client_name = client_name; ev.event_date = event_date
        ev.quoted_amount = quoted_amount; ev.status = EventStatus(status); ev.notes = notes
        ev.payment_due_dates = payment_due_dates
        ev.last_reminder_sent = last_reminder_sent
        ev.reminder_notes = reminder_notes
        ev.client_id = client_id
        ev.event_type = event_type; ev.location = location; ev.referral_source = referral_source
        ev.delivery_status = delivery_status
        return ev

    def set_event_reminder(self, event_id: int, reminder_date: date,
                           notes: Optional[str] = None) -> Optional[Event]:
        """Log that a payment reminder was sent for this event (Phase 1.5)."""
        ev = self.get_event(event_id)
        if ev is None:
            return None
        ev.last_reminder_sent = reminder_date
        ev.reminder_notes = notes
        # Persist by calling update_event with current fields
        return self.update_event(
            event_id=event_id,
            name=ev.name, client_name=ev.client_name, event_date=ev.event_date,
            quoted_amount=ev.quoted_amount, status=ev.status.value, notes=ev.notes,
            event_type=ev.event_type, location=ev.location, referral_source=ev.referral_source,
            payment_due_dates=ev.payment_due_dates,
            last_reminder_sent=reminder_date,
            reminder_notes=notes,
        )

    def set_event_payment_schedule(self, event_id: int,
                                   payment_due_dates: Optional[str]) -> Optional[Event]:
        """Store the payment schedule (JSON list of ISO dates) for an event (Phase 1.2)."""
        ev = self.get_event(event_id)
        if ev is None:
            return None
        return self.update_event(
            event_id=event_id,
            name=ev.name, client_name=ev.client_name, event_date=ev.event_date,
            quoted_amount=ev.quoted_amount, status=ev.status.value, notes=ev.notes,
            event_type=ev.event_type, location=ev.location, referral_source=ev.referral_source,
            payment_due_dates=payment_due_dates,
            last_reminder_sent=ev.last_reminder_sent,
            reminder_notes=ev.reminder_notes,
        )

    def delete_event(self, event_id: int):
        ev = self.get_event(event_id)
        ev_name = ev.name if ev else str(event_id)
        for p in self.list_payments(event_id=event_id):
            self._del(self._payments, p.id)
        for e in self.list_expenses(event_id=event_id):
            self._del(self._expenses, e.id)
        self._del(self._events, event_id)
        self.log_audit("event", event_id, "delete", f"Deleted event '{ev_name}'")

    # ── Clients (Phase 2) ─────────────────────────────────────────────────────

    def list_clients(self) -> list[Client]:
        return [
            Client(
                id=int(r["id"]),
                name=r["name"],
                phone=r.get("phone") or None,
                email=r.get("email") or None,
                address=r.get("address") or None,
                notes=r.get("notes") or None,
                created_at=r.get("created_at", ""),
            )
            for r in _get_records(self._clients, _T_CLIENTS)
            if r.get("id")
        ]

    def get_client(self, client_id: int) -> Optional[Client]:
        for c in self.list_clients():
            if c.id == client_id:
                return c
        return None

    def create_client(self, name: str, phone: Optional[str] = None,
                      email: Optional[str] = None, address: Optional[str] = None,
                      notes: Optional[str] = None) -> Client:
        nid = _next_id(_get_records(self._clients, _T_CLIENTS))
        now = _now()
        self._clients.append_row([nid, name, phone or "", email or "",
                                   address or "", notes or "", now])
        self.log_audit("client", nid, "create", f"Created client '{name}'")
        return Client(id=nid, name=name, phone=phone, email=email,
                      address=address, notes=notes, created_at=now)

    def update_client(self, client_id: int, name: str, phone: Optional[str] = None,
                      email: Optional[str] = None, address: Optional[str] = None,
                      notes: Optional[str] = None) -> Optional[Client]:
        c = self.get_client(client_id)
        if c is None:
            return None
        row = _find_row(self._clients, client_id)
        self._clients.update([[client_id, name, phone or "", email or "",
                                address or "", notes or "", c.created_at]], f"A{row}")
        c.name = name; c.phone = phone; c.email = email
        c.address = address; c.notes = notes
        return c

    def delete_client(self, client_id: int):
        c = self.get_client(client_id)
        name = c.name if c else str(client_id)
        self._del(self._clients, client_id)
        self.log_audit("client", client_id, "delete", f"Deleted client '{name}'")

    # ── Payments ──────────────────────────────────────────────────────────────

    def list_payments(self, event_id: Optional[int] = None) -> list[EventPayment]:
        payments = [
            EventPayment(
                id=int(r["id"]),
                event_id=int(r["event_id"]),
                amount=_parse_float(r.get("amount", 0)),
                payment_date=_parse_date(r.get("payment_date")) or date.today(),
                notes=r.get("notes") or None,
                created_at=r.get("created_at", ""),
            )
            for r in _get_records(self._payments, _T_PAYMENTS)
            if r.get("id")
        ]
        if event_id is not None:
            payments = [p for p in payments if p.event_id == event_id]
        return payments

    def create_payment(self, event_id: int, amount: float,
                       payment_date: date, notes: Optional[str] = None) -> EventPayment:
        nid = _next_id(_get_records(self._payments, _T_PAYMENTS))
        now = _now()
        self._payments.append_row([nid, event_id, amount, payment_date.isoformat(), notes or "", now])
        return EventPayment(id=nid, event_id=event_id, amount=amount,
                            payment_date=payment_date, notes=notes, created_at=now)

    def delete_payment(self, payment_id: int):
        self._del(self._payments, payment_id)

    # ── Expenses ──────────────────────────────────────────────────────────────

    def list_expenses(self, event_id: Optional[int] = None, category_id: Optional[int] = None,
                      scope: Optional[str] = None, status: Optional[str] = None,
                      date_from: Optional[date] = None, date_to: Optional[date] = None) -> list[Expense]:
        expenses: list[Expense] = []
        for r in _get_records(self._expenses, _T_EXPENSES):
            if not r.get("id"):
                continue
            expenses.append(Expense(
                id=int(r["id"]),
                date=_parse_date(r.get("date")) or date.today(),
                event_id=_parse_int(r.get("event_id", "")),
                category_id=int(r["category_id"]),
                scope=CategoryScope(r.get("scope", "event")),
                payment_status=PaymentStatus(r.get("payment_status", "paid")),
                amount=_parse_float(r.get("amount", 0)),
                paid_amount=_parse_float(r.get("paid_amount", 0)),
                paid_to=r.get("paid_to") or None,
                notes=r.get("notes") or None,
                created_at=r.get("created_at", ""),
                payee_id=_parse_int(r.get("payee_id", "")),
                is_recurring=_parse_bool(r.get("is_recurring", "FALSE")),
                recurring_day=_parse_int(r.get("recurring_day", "")),
                payment_type=r.get("payment_type") or None,
            ))
        if event_id is not None:
            expenses = [e for e in expenses if e.event_id == event_id]
        if category_id is not None:
            expenses = [e for e in expenses if e.category_id == category_id]
        if scope is not None:
            expenses = [e for e in expenses if e.scope.value == scope]
        if status is not None:
            expenses = [e for e in expenses if e.payment_status.value == status]
        if date_from is not None:
            expenses = [e for e in expenses if e.date >= date_from]
        if date_to is not None:
            expenses = [e for e in expenses if e.date <= date_to]
        expenses.sort(key=lambda e: e.date, reverse=True)
        return expenses

    def get_expense(self, expense_id: int) -> Optional[Expense]:
        for e in self.list_expenses():
            if e.id == expense_id:
                return e
        return None

    def create_expense(self, date_: date, category_id: int, scope: str,
                       payment_status: str, amount: float, event_id: Optional[int] = None,
                       paid_amount: float = 0.0, paid_to: Optional[str] = None,
                       notes: Optional[str] = None, payee_id: Optional[int] = None,
                       is_recurring: bool = False,
                       recurring_day: Optional[int] = None,
                       payment_type: Optional[str] = None) -> Expense:
        nid = _next_id(_get_records(self._expenses, _T_EXPENSES))
        now = _now()
        self._expenses.append_row([
            nid, date_.isoformat(), event_id or "", category_id, scope,
            payment_status, amount, paid_amount, paid_to or "", notes or "", now,
            payee_id or "",
            "TRUE" if is_recurring else "FALSE",
            recurring_day or "",
            payment_type or "",
        ])
        self.log_audit("expense", nid, "create",
                       f"Created {'recurring ' if is_recurring else ''}expense ₹{amount:,.0f} [{scope}]")
        return Expense(id=nid, date=date_, event_id=event_id, category_id=category_id,
                       scope=CategoryScope(scope), payment_status=PaymentStatus(payment_status),
                       amount=amount, paid_amount=paid_amount, paid_to=paid_to,
                       notes=notes, created_at=now, payee_id=payee_id,
                       is_recurring=is_recurring, recurring_day=recurring_day,
                       payment_type=payment_type or None)

    def update_expense(self, expense_id: int, date_: date, category_id: int, scope: str,
                       payment_status: str, amount: float, event_id: Optional[int] = None,
                       paid_amount: float = 0.0, paid_to: Optional[str] = None,
                       notes: Optional[str] = None, payee_id: Optional[int] = None,
                       is_recurring: Optional[bool] = None,
                       recurring_day: Optional[int] = None,
                       payment_type: Optional[str] = None) -> Optional[Expense]:
        exp = self.get_expense(expense_id)
        if exp is None:
            return None
        if payee_id is None:
            payee_id = exp.payee_id
        if is_recurring is None:
            is_recurring = exp.is_recurring
        if recurring_day is None:
            recurring_day = exp.recurring_day
        if payment_type is None:
            payment_type = exp.payment_type
        row = _find_row(self._expenses, expense_id)
        self._expenses.update([[
            expense_id, date_.isoformat(), event_id or "", category_id, scope,
            payment_status, amount, paid_amount, paid_to or "", notes or "", exp.created_at,
            payee_id or "",
            "TRUE" if is_recurring else "FALSE",
            recurring_day or "",
            payment_type or "",
        ]], f"A{row}")
        exp.date = date_; exp.event_id = event_id; exp.category_id = category_id
        exp.scope = CategoryScope(scope); exp.payment_status = PaymentStatus(payment_status)
        exp.amount = amount; exp.paid_amount = paid_amount
        exp.paid_to = paid_to; exp.notes = notes; exp.payee_id = payee_id
        exp.is_recurring = is_recurring; exp.recurring_day = recurring_day
        exp.payment_type = payment_type or None
        return exp

    def delete_expense(self, expense_id: int):
        self._del(self._expenses, expense_id)
        self.log_audit("expense", expense_id, "delete", f"Deleted expense #{expense_id}")

    # ── Payees (Phase 2) ──────────────────────────────────────────────────────

    def list_payees(self) -> list[Payee]:
        return [
            Payee(
                id=int(r["id"]),
                name=r["name"],
                payee_type=r.get("payee_type") or "freelancer",
                phone=r.get("phone") or None,
                email=r.get("email") or None,
                notes=r.get("notes") or None,
                created_at=r.get("created_at", ""),
            )
            for r in _get_records(self._payees, _T_PAYEES)
            if r.get("id")
        ]

    def get_payee(self, payee_id: int) -> Optional[Payee]:
        for p in self.list_payees():
            if p.id == payee_id:
                return p
        return None

    def create_payee(self, name: str, payee_type: str = "freelancer",
                     phone: Optional[str] = None, email: Optional[str] = None,
                     notes: Optional[str] = None) -> Payee:
        nid = _next_id(_get_records(self._payees, _T_PAYEES))
        now = _now()
        self._payees.append_row([nid, name, payee_type, phone or "",
                                  email or "", notes or "", now])
        self.log_audit("payee", nid, "create", f"Created {payee_type} '{name}'")
        return Payee(id=nid, name=name, payee_type=payee_type, phone=phone,
                     email=email, notes=notes, created_at=now)

    def update_payee(self, payee_id: int, name: str, payee_type: str = "freelancer",
                     phone: Optional[str] = None, email: Optional[str] = None,
                     notes: Optional[str] = None) -> Optional[Payee]:
        p = self.get_payee(payee_id)
        if p is None:
            return None
        row = _find_row(self._payees, payee_id)
        self._payees.update([[payee_id, name, payee_type, phone or "",
                               email or "", notes or "", p.created_at]], f"A{row}")
        p.name = name; p.payee_type = payee_type; p.phone = phone
        p.email = email; p.notes = notes
        return p

    def delete_payee(self, payee_id: int):
        p = self.get_payee(payee_id)
        name = p.name if p else str(payee_id)
        self._del(self._payees, payee_id)
        self.log_audit("payee", payee_id, "delete", f"Deleted payee '{name}'")

    # ── Leads (Phase 4) ───────────────────────────────────────────────────────

    def list_leads(self, status: Optional[str] = None) -> list[Lead]:
        leads = [
            Lead(
                id=int(r["id"]),
                client_name=r.get("client_name", ""),
                contact=r.get("contact", ""),
                event_type=r.get("event_type", ""),
                tentative_date=_parse_date(r.get("tentative_date")),
                source=r.get("source", ""),
                status=r.get("status", "new"),
                quoted_amount=_parse_float(r.get("quoted_amount", 0)),
                notes=r.get("notes", ""),
                created_at=r.get("created_at", ""),
                client_id=_parse_int(r.get("client_id", "")),
                num_events=int(_parse_float(r.get("num_events", 0))),
                revised_quote=_parse_float(r.get("revised_quote", 0)),
                follow_ups=r.get("follow_ups", ""),
                rejection_reason=r.get("rejection_reason", ""),
                meta_campaign=_parse_bool(r.get("meta_campaign", "FALSE")),
                referral_name=r.get("referral_name", ""),
                followup_status=r.get("followup_status", "pending") or "pending",
                followup_date=_parse_date(r.get("followup_date")),
            )
            for r in _get_records(self._leads, _T_LEADS)
            if r.get("id")
        ]
        if status:
            leads = [l for l in leads if l.status == status]
        leads.sort(key=lambda l: l.created_at, reverse=True)
        return leads

    def get_lead(self, lead_id: int) -> Optional[Lead]:
        for l in self.list_leads():
            if l.id == lead_id:
                return l
        return None

    def create_lead(self, client_name: str, contact: str = "",
                    event_type: str = "", tentative_date: Optional[date] = None,
                    source: str = "", status: str = "new",
                    quoted_amount: float = 0.0, notes: str = "",
                    client_id: Optional[int] = None,
                    num_events: int = 0, revised_quote: float = 0.0,
                    follow_ups: str = "", rejection_reason: str = "",
                    meta_campaign: bool = False, referral_name: str = "",
                    followup_status: str = "pending",
                    followup_date: Optional[date] = None) -> Lead:
        nid = _next_id(_get_records(self._leads, _T_LEADS))
        now = _now()
        self._leads.append_row([
            nid, client_name, contact, event_type,
            tentative_date.isoformat() if tentative_date else "",
            source, status, quoted_amount, notes, now, client_id or "",
            num_events, revised_quote, follow_ups, rejection_reason,
            "TRUE" if meta_campaign else "FALSE", referral_name,
            followup_status,
            followup_date.isoformat() if followup_date else "",
        ])
        self.log_audit("lead", nid, "create", f"Created lead for '{client_name}'")
        return Lead(id=nid, client_name=client_name, contact=contact,
                    event_type=event_type, tentative_date=tentative_date,
                    source=source, status=status, quoted_amount=quoted_amount,
                    notes=notes, created_at=now, client_id=client_id,
                    num_events=num_events, revised_quote=revised_quote,
                    follow_ups=follow_ups, rejection_reason=rejection_reason,
                    meta_campaign=meta_campaign, referral_name=referral_name,
                    followup_status=followup_status, followup_date=followup_date)

    def update_lead(self, lead_id: int, client_name: str, contact: str = "",
                    event_type: str = "", tentative_date: Optional[date] = None,
                    source: str = "", status: str = "new",
                    quoted_amount: float = 0.0, notes: str = "",
                    client_id: Optional[int] = None,
                    num_events: int = 0, revised_quote: float = 0.0,
                    follow_ups: str = "", rejection_reason: str = "",
                    meta_campaign: bool = False, referral_name: str = "",
                    followup_status: str = "pending",
                    followup_date: Optional[date] = None) -> Optional[Lead]:
        lead = self.get_lead(lead_id)
        if lead is None:
            return None
        row = _find_row(self._leads, lead_id)
        self._leads.update([[
            lead_id, client_name, contact, event_type,
            tentative_date.isoformat() if tentative_date else "",
            source, status, quoted_amount, notes, lead.created_at, client_id or "",
            num_events, revised_quote, follow_ups, rejection_reason,
            "TRUE" if meta_campaign else "FALSE", referral_name,
            followup_status,
            followup_date.isoformat() if followup_date else "",
        ]], f"A{row}")
        lead.client_name = client_name; lead.contact = contact
        lead.event_type = event_type; lead.tentative_date = tentative_date
        lead.source = source; lead.status = status
        lead.quoted_amount = quoted_amount; lead.notes = notes; lead.client_id = client_id
        lead.num_events = num_events; lead.revised_quote = revised_quote
        lead.follow_ups = follow_ups; lead.rejection_reason = rejection_reason
        lead.meta_campaign = meta_campaign; lead.referral_name = referral_name
        lead.followup_status = followup_status; lead.followup_date = followup_date
        return lead

    def delete_lead(self, lead_id: int):
        lead = self.get_lead(lead_id)
        name = lead.client_name if lead else str(lead_id)
        self._del(self._leads, lead_id)
        self.log_audit("lead", lead_id, "delete", f"Deleted lead '{name}'")

    # ── Enrichment ────────────────────────────────────────────────────────────

    def enrich_event(self, event: Event) -> Event:
        """Populate event.payments and event.expenses with category backlinks."""
        cats = {c.id: c for c in self.list_categories()}
        event.payments = self.list_payments(event_id=event.id)
        event.expenses = self.list_expenses(event_id=event.id)
        for exp in event.expenses:
            exp.category = cats.get(exp.category_id)
        return event

    def list_events_enriched(self) -> list[Event]:
        """Load all events with payments and expenses in ~4 API calls."""
        events   = self.list_events()
        all_pays = self.list_payments()
        all_exps = self.list_expenses()
        cats     = {c.id: c for c in self.list_categories()}

        pays_by_event: dict[int, list[EventPayment]] = {}
        for p in all_pays:
            pays_by_event.setdefault(p.event_id, []).append(p)

        exps_by_event: dict[int, list[Expense]] = {}
        for e in all_exps:
            e.category = cats.get(e.category_id)
            if e.event_id:
                exps_by_event.setdefault(e.event_id, []).append(e)

        for ev in events:
            ev.payments = pays_by_event.get(ev.id, [])
            ev.expenses = exps_by_event.get(ev.id, [])
        return events

    # ── Settings (Phase 3) ────────────────────────────────────────────────────

    _SETTING_DEFAULTS: dict[str, str] = {
        "studio_name":           "Life in Frame",
        "studio_sub":            "Studio Finance",
        "currency_symbol":       "₹",
        "ar_grace_days":         "0",
        "reminder_cadence_days": "7",
    }

    def get_settings_dict(self) -> dict[str, str]:
        """Return all studio settings as a key→value dict, with defaults filled in."""
        stored = {
            r["key"]: r.get("value", "")
            for r in _get_records(self._settings, _T_SETTINGS)
            if r.get("key")
        }
        result = dict(self._SETTING_DEFAULTS)
        result.update(stored)
        return result

    def set_settings(self, settings_dict: dict[str, str]) -> None:
        """Upsert settings. Creates new rows or updates existing ones."""
        records = _get_records(self._settings, _T_SETTINGS)
        # key → (sheet_row_1based, row_id)
        row_map: dict[str, tuple[int, int]] = {
            r["key"]: (i + 2, int(r.get("id") or 0))
            for i, r in enumerate(records)
            if r.get("key")
        }
        max_id = max((int(r["id"]) for r in records if r.get("id")), default=0)
        now = _now()
        for key, value in settings_dict.items():
            if key in row_map:
                sheet_row, _ = row_map[key]
                self._settings.update([[key, str(value), now]], f"B{sheet_row}")
            else:
                max_id += 1
                self._settings.append_row([max_id, key, str(value), now])

    def seed_settings_if_empty(self) -> None:
        """Populate default settings if the settings sheet is empty."""
        if _get_records(self._settings, _T_SETTINGS):
            return
        self.set_settings(self._SETTING_DEFAULTS)

    # ── Audit log (Phase 3) ───────────────────────────────────────────────────

    def log_audit(self, entity_type: str, entity_id: int,
                  action: str, summary: str) -> None:
        """Append one audit entry. Best-effort — never raises."""
        try:
            recs = _get_records(self._audit, _T_AUDIT)
            nid = _next_id(recs)
            self._audit.append_row([nid, _now(), entity_type, entity_id, action, summary])
        except Exception as exc:
            logging.warning("Audit log write failed: %s", exc)

    def list_audit(self, limit: int = 200,
                   entity_type: Optional[str] = None) -> list[AuditEntry]:
        entries = [
            AuditEntry(
                id=int(r["id"]),
                timestamp=r.get("timestamp", ""),
                entity_type=r.get("entity_type", ""),
                entity_id=int(r.get("entity_id") or 0),
                action=r.get("action", ""),
                summary=r.get("summary", ""),
            )
            for r in _get_records(self._audit, _T_AUDIT)
            if r.get("id")
        ]
        if entity_type:
            entries = [e for e in entries if e.entity_type == entity_type]
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    # ── Recurring expenses (Phase 3) ──────────────────────────────────────────

    def generate_recurring_expenses(self, target_year: int,
                                    target_month: int) -> list[Expense]:
        """Create expense instances from recurring templates for the target month.

        Skips a template if an expense with the same category/scope/event/date
        already exists in the target month (dedup guard).
        Returns the list of newly created expenses.
        """
        from calendar import monthrange
        templates_list = [e for e in self.list_expenses() if e.is_recurring]
        if not templates_list:
            return []
        # Non-recurring expenses already in the target month (for dedup)
        existing = [
            e for e in self.list_expenses()
            if not e.is_recurring
            and e.date.year == target_year
            and e.date.month == target_month
        ]
        _, month_days = monthrange(target_year, target_month)
        created: list[Expense] = []
        for tmpl in templates_list:
            day = min(tmpl.recurring_day or 1, month_days)
            target_date = date(target_year, target_month, day)
            # Dedup: skip if same category+scope+event+date already exists
            if any(
                e.category_id == tmpl.category_id
                and e.scope == tmpl.scope
                and e.event_id == tmpl.event_id
                and e.date == target_date
                for e in existing
            ):
                continue
            new_exp = self.create_expense(
                date_=target_date,
                category_id=tmpl.category_id,
                scope=tmpl.scope.value,
                payment_status="pending",
                amount=tmpl.amount,
                event_id=tmpl.event_id,
                paid_to=tmpl.paid_to,
                notes=(f"[Auto-generated] {tmpl.notes or ''}").strip(),
                payee_id=tmpl.payee_id,
                is_recurring=False,
            )
            created.append(new_exp)
        return created

    # ── Private ───────────────────────────────────────────────────────────────

    def _del(self, ws: gspread.Worksheet, record_id: int):
        row = _find_row(ws, record_id)
        if row != -1:
            ws.delete_rows(row)


# ─── FastAPI dependency ──────────────────────────────────────────────────────

_db_instance: Optional[SheetDB] = None


def get_db() -> SheetDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = SheetDB()
    return _db_instance
