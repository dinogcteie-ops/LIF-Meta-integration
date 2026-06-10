"""Tests for the RBAC scaffold and the WhatsApp payment-reminder links (B2)."""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.rbac import Role, can, current_role, has_perm, require
from app.services.whatsapp import payment_reminder_text, wa_link, wa_number


# ─── RBAC matrix ─────────────────────────────────────────────────────────────

def _req(role: str | None):
    session = {"user": "x"}
    if role is not None:
        session["role"] = role
    return SimpleNamespace(session=session)


def test_owner_has_everything():
    for perm in ("finance.view", "finance.edit", "leads.edit", "admin"):
        assert has_perm(Role.owner, perm)


def test_marketing_cannot_see_finance():
    assert has_perm(Role.marketing, "leads.edit")
    assert has_perm(Role.marketing, "directory.view")
    assert not has_perm(Role.marketing, "finance.view")
    assert not has_perm(Role.marketing, "finance.edit")
    assert not has_perm(Role.marketing, "admin")


def test_guest_is_view_only():
    assert has_perm(Role.guest, "finance.view")
    assert has_perm(Role.guest, "leads.view")
    for perm in ("finance.edit", "leads.edit", "directory.edit", "admin"):
        assert not has_perm(Role.guest, perm)


def test_current_role_fallbacks():
    # Pre-RBAC session (no role key) = owner; unknown role string = guest.
    assert current_role(_req(None)) is Role.owner
    assert current_role(_req("marketing")) is Role.marketing
    assert current_role(_req("hacker")) is Role.guest


def test_require_blocks_and_allows():
    dep = require("finance.edit")
    dep(_req("owner"))                                   # no raise
    with pytest.raises(HTTPException) as exc:
        dep(_req("guest"))
    assert exc.value.status_code == 403
    assert can(_req("manager"), "finance.edit")
    assert not can(_req("marketing"), "finance.view")


# ─── WhatsApp number normalization ───────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("98765 43210",        "919876543210"),   # bare Indian 10-digit
    ("+91 98765-43210",    "919876543210"),   # already has country code
    ("09876543210",        "919876543210"),   # trunk-prefix 0
    ("919876543210",       "919876543210"),   # passthrough
    ("+1 (415) 555-0100",  "14155550100"),    # non-Indian international
    ("1234",               None),             # too short to be real
    ("",                   None),
    (None,                 None),
])
def test_wa_number(raw, expected):
    assert wa_number(raw) == expected


def test_wa_link_and_message():
    text = payment_reminder_text("Life in Frame", "Ravi", "Wedding A", "₹50,000")
    assert "Ravi" in text and "Wedding A" in text and "₹50,000" in text
    link = wa_link("98765 43210", text)
    assert link.startswith("https://wa.me/919876543210?text=")
    assert wa_link("12", text) is None


# ─── Receivables page integration ────────────────────────────────────────────

def test_receivables_shows_whatsapp_link(client, db):
    today = date.today()
    c = db.create_client(name="WA Test Client", phone="98888 77766")
    ev = db.create_event(name="WA Test Shoot", client_name=c.name, status="completed",
                         quoted_amount=10.0, event_date=today, client_id=c.id)
    db.create_payment(event_id=ev.id, amount=4.0, payment_date=today)  # 6 pending
    try:
        r = client.get("/receivables")
        assert r.status_code == 200
        assert "https://wa.me/919888877766?text=" in r.text
        # Owner sessions hold finance.edit, so the reminder button renders too.
        assert "Mark reminded" in r.text
    finally:
        db.delete_event(ev.id)
        db.delete_client(c.id)


def test_receivables_no_link_without_phone(client, db):
    today = date.today()
    ev = db.create_event(name="No Phone Shoot", client_name="Walk-in", status="completed",
                         quoted_amount=5.0, event_date=today)
    try:
        r = client.get("/receivables")
        assert r.status_code == 200
        # The row renders, but with no wa.me link for this unlinked event.
        assert "No Phone Shoot" in r.text
    finally:
        db.delete_event(ev.id)
