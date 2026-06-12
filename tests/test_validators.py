"""Unit tests for app/validators.py + route-level validation behaviour."""
from datetime import date, timedelta

from app.validators import (
    find_phone_match,
    normalize_phone,
    parse_amount,
    parse_date_safe,
    parse_enum,
    valid_email,
)
from app.enums import LeadStatus


# ─── normalize_phone ──────────────────────────────────────────────────────────

def test_normalize_phone_variants_collapse():
    assert normalize_phone("+91 98765 43210") == "9876543210"
    assert normalize_phone("p:+919876543210") == "9876543210"
    assert normalize_phone("P:919876543210") == "9876543210"
    assert normalize_phone("98765-43210") == "9876543210"
    assert normalize_phone("09876543210") == "9876543210"


def test_normalize_phone_short_and_empty():
    assert normalize_phone("") == ""
    assert normalize_phone(None) == ""
    assert normalize_phone("12345") == "12345"   # short → returned as-is


# ─── valid_email ──────────────────────────────────────────────────────────────

def test_valid_email():
    assert valid_email("a@b.co")
    assert valid_email("  user.name+tag@example.com  ")
    assert not valid_email("asdf")
    assert not valid_email("test@")
    assert not valid_email("a@b")          # no TLD
    assert not valid_email("")


# ─── parse_amount ─────────────────────────────────────────────────────────────

def test_parse_amount_accepts_valid():
    v, err = parse_amount(2.5, "Quote")
    assert v == 2.5 and err is None
    v, err = parse_amount(0, "Quote")
    assert v == 0 and err is None


def test_parse_amount_accepts_rupee_amounts():
    # All money is in rupees now — a ₹2.5L quote (250000) and a ₹50L quote
    # must pass (these were wrongly rejected under the old ₹1L-lakh ceiling).
    for amt in (250000, 5_000_000, 99_999_999):
        v, err = parse_amount(amt, "Quote")
        assert err is None and v == amt


def test_parse_amount_rejects_negative_nan_huge():
    _, err = parse_amount(-1, "Quote")
    assert err and "negative" in err
    _, err = parse_amount(float("nan"), "Quote")
    assert err
    _, err = parse_amount(float("inf"), "Quote")
    assert err
    _, err = parse_amount(10**9, "Quote")
    assert err and "too large" in err


# ─── parse_date_safe ──────────────────────────────────────────────────────────

def test_parse_date_safe():
    d, err = parse_date_safe("2026-06-15", "Date")
    assert d == date(2026, 6, 15) and err is None
    d, err = parse_date_safe("", "Date")
    assert d is None and err is None
    d, err = parse_date_safe("not-a-date", "Date")
    assert d is None and err
    # Typo years rejected
    d, err = parse_date_safe("0203-01-01", "Date")
    assert d is None and err
    far = (date.today() + timedelta(days=365 * 25)).isoformat()
    d, err = parse_date_safe(far, "Date")
    assert d is None and err


# ─── parse_enum ───────────────────────────────────────────────────────────────

def test_parse_enum():
    v, err = parse_enum(LeadStatus, "new", "lead status")
    assert v == LeadStatus.new and err is None
    v, err = parse_enum(LeadStatus, "", "lead status", default=LeadStatus.new)
    assert v == LeadStatus.new and err is None
    v, err = parse_enum(LeadStatus, "bogus", "lead status")
    assert v is None and err and "bogus" in err


# ─── find_phone_match ─────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, id, name, contact=None, phone=None):
        self.id, self.client_name, self.name = id, name, name
        if contact is not None:
            self.contact = contact
        if phone is not None:
            self.phone = phone


def test_find_phone_match_leads_and_clients():
    leads = [_Stub(1, "A", contact="+91 98765 43210")]
    assert find_phone_match("p:919876543210", leads).id == 1
    clients = [_Stub(7, "B", phone="98765 43210")]
    assert find_phone_match("9876543210", clients).id == 7
    assert find_phone_match("1234567890", leads) is None
    # Short keys never match (avoid junk collisions)
    assert find_phone_match("123", [_Stub(2, "C", contact="123")]) is None


# ─── Route-level behaviour: bad input flashes, never 500s ────────────────────

def test_create_lead_negative_quote_rejected(client, db):
    before = len(db.list_leads())
    r = client.post("/leads", data={
        "client_name": "Neg Quote", "quoted_amount": "-5",
    })
    assert r.status_code == 200          # redirect chain ends on a page
    assert len(db.list_leads()) == before


def test_create_lead_bad_date_rejected(client, db):
    before = len(db.list_leads())
    r = client.post("/leads", data={
        "client_name": "Bad Date", "tentative_date": "0203-01-01",
    })
    assert r.status_code == 200
    assert len(db.list_leads()) == before


def test_create_lead_duplicate_phone_warns_but_creates(client, db):
    r = client.post("/leads", data={
        "client_name": "Original", "contact": "+91 91234 56789",
    })
    assert r.status_code == 200
    before = len(db.list_leads())
    r = client.post("/leads", data={
        "client_name": "Dup", "contact": "912345 6789 +91",
    })
    assert r.status_code == 200
    assert len(db.list_leads()) == before + 1   # created, not blocked


def test_create_event_negative_amount_rejected(client, db):
    before = len(db.list_events())
    r = client.post("/events", data={
        "name": "Neg Event", "quoted_amount": "-3",
    })
    assert r.status_code == 200
    assert len(db.list_events()) == before


def test_create_expense_bad_enum_rejected(client, db):
    cats = db.list_categories()
    before = len(db.list_expenses(include_estimates=True))
    r = client.post("/expenses", data={
        "date": "2026-01-10", "scope": "bogus-scope",
        "category_id": str(cats[0].id), "amount": "1.0",
    })
    assert r.status_code == 200
    assert len(db.list_expenses(include_estimates=True)) == before


def test_create_client_bad_email_rejected(client, db):
    before = len(db.list_clients())
    r = client.post("/clients", data={
        "name": "Bad Email", "email": "not-an-email",
    })
    assert r.status_code == 200
    assert len(db.list_clients()) == before
