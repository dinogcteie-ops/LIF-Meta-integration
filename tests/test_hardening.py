"""Tests for the security/automation hardening pass:

  * verify_password honours APP_PASSWORD_HASH (bcrypt) with plaintext fallback
  * /login locks an IP out after repeated failures
  * /jobs recurring-expense posting is due-date aware and idempotent
  * /inquiry honeypot + per-IP throttle silently drop spam
"""
from datetime import date, timedelta

import bcrypt
from fastapi.testclient import TestClient

import app.auth as auth_mod
from app.main import app
from app.routes.auth import _reset_throttle
from app.routes.portal import _reset_inquiry_throttle
from app.services.recurring import post_due_recurring


# ─── verify_password ─────────────────────────────────────────────────────────

def test_verify_password_plaintext_fallback():
    assert auth_mod.verify_password("testpass") is True
    assert auth_mod.verify_password("wrong") is False


def test_verify_password_prefers_hash(monkeypatch):
    h = bcrypt.hashpw(b"hash-only-secret", bcrypt.gensalt()).decode()
    monkeypatch.setattr(auth_mod.settings, "app_password_hash", h)
    assert auth_mod.verify_password("hash-only-secret") is True
    # With a hash configured, the plaintext APP_PASSWORD must no longer work.
    assert auth_mod.verify_password("testpass") is False


# ─── Login lockout ───────────────────────────────────────────────────────────

def test_login_lockout_after_repeated_failures():
    _reset_throttle()
    try:
        with TestClient(app, follow_redirects=False) as c:
            for _ in range(5):
                r = c.post("/login", data={"password": "nope"})
                assert r.status_code == 401
            # Locked now — even the correct password is rejected with 429.
            r = c.post("/login", data={"password": "testpass"})
            assert r.status_code == 429
            assert "Try again" in r.text
    finally:
        _reset_throttle()


def test_login_success_clears_failures():
    _reset_throttle()
    try:
        with TestClient(app, follow_redirects=False) as c:
            for _ in range(3):
                assert c.post("/login", data={"password": "nope"}).status_code == 401
            assert c.post("/login", data={"password": "testpass"}).status_code == 303
    finally:
        _reset_throttle()


# ─── Recurring expense posting ───────────────────────────────────────────────

def test_recurring_posting_is_due_aware_and_idempotent(db):
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    cat = db.list_categories(active_only=True)[0]
    created_ids = []
    tpl = db.create_expense(
        date_=last_month, category_id=cat.id, scope="company",
        payment_status="paid", amount=123.45, paid_amount=123.45,
        paid_to="Test Landlord", notes="office rent",
        is_recurring=True, recurring_day=min(today.day, 28),
    )
    created_ids.append(tpl.id)
    try:
        preview = post_due_recurring(db, dry_run=True)
        assert preview["posted"] == 1
        assert preview["details"][0]["template_id"] == tpl.id

        result = post_due_recurring(db)
        assert result["posted"] == 1
        copies = [e for e in db.list_expenses()
                  if f"[auto-recurring #{tpl.id}]" in (e.notes or "")]
        assert len(copies) == 1
        copy = copies[0]
        created_ids.append(copy.id)
        assert copy.payment_status.value == "pending"
        assert copy.is_recurring is False
        assert copy.amount == 123.45
        assert (copy.date.year, copy.date.month) == (today.year, today.month)

        # Second run in the same month is a no-op.
        again = post_due_recurring(db)
        assert again["posted"] == 0
    finally:
        for eid in created_ids:
            db.delete_expense(eid)


def test_recurring_not_due_yet_is_skipped(db):
    today = date.today()
    if today.day >= 28:   # no "future day" exists this month; nothing to test
        return
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    cat = db.list_categories(active_only=True)[0]
    tpl = db.create_expense(
        date_=last_month, category_id=cat.id, scope="company",
        payment_status="paid", amount=50.0, paid_amount=50.0,
        is_recurring=True, recurring_day=today.day + 1,
    )
    try:
        result = post_due_recurring(db, dry_run=True)
        assert all(d["template_id"] != tpl.id for d in result["details"])
    finally:
        db.delete_expense(tpl.id)


# ─── Inquiry spam protection ─────────────────────────────────────────────────

def test_inquiry_honeypot_drops_silently(db):
    _reset_inquiry_throttle()
    before = len(db.list_leads())
    with TestClient(app) as c:
        r = c.post("/inquiry", data={"client_name": "Bot", "website": "spam.example"})
        assert r.status_code == 200
        assert "Thank You" in r.text          # indistinguishable from success
    assert len(db.list_leads()) == before     # …but nothing was created


def test_inquiry_throttle_caps_submissions(db):
    _reset_inquiry_throttle()
    created = []
    try:
        with TestClient(app) as c:
            before = len(db.list_leads())
            for i in range(6):
                r = c.post("/inquiry", data={"client_name": f"Visitor {i}"})
                assert r.status_code == 200
            leads = db.list_leads()
            created = [l.id for l in leads if (l.client_name or "").startswith("Visitor ")]
            # 5 allowed per hour per IP; the 6th was silently dropped.
            assert len(leads) - before == 5
    finally:
        _reset_inquiry_throttle()
        for lid in created:
            db.delete_lead(lid)
