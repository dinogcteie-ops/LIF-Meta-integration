"""Tests for Google sign-in (OAuth flow + email→role mapping) and the
consolidated recurring-expense posting."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.routes.auth as auth_routes
from app.main import app
from app.rbac import Role, role_for_email
from app.services.recurring import generate_for_month, post_due_recurring


# ─── Email → role mapping ────────────────────────────────────────────────────

def test_seeded_owner_emails_resolve(db):
    settings_dict = db.get_settings_dict()
    assert role_for_email("dinogcteie@gmail.com", settings_dict) is Role.owner
    assert role_for_email("lifeinframe.in@gmail.com", settings_dict) is Role.owner
    # Case/whitespace-insensitive
    assert role_for_email("  DinogCteie@Gmail.COM ", settings_dict) is Role.owner


def test_unlisted_email_resolves_to_none(db):
    assert role_for_email("stranger@example.com", db.get_settings_dict()) is None
    assert role_for_email("", db.get_settings_dict()) is None
    assert role_for_email(None, db.get_settings_dict()) is None


def test_role_priority_most_privileged_wins():
    settings_dict = {"role_owners": "x@y.com", "role_guests": "x@y.com, g@y.com"}
    assert role_for_email("x@y.com", settings_dict) is Role.owner
    assert role_for_email("g@y.com", settings_dict) is Role.guest


# ─── OAuth flow ──────────────────────────────────────────────────────────────

def _enable_google(monkeypatch):
    s = auth_routes.get_settings()
    monkeypatch.setattr(s, "google_client_id", "test-client-id")
    monkeypatch.setattr(s, "google_client_secret", "test-secret")


def _disable_google(monkeypatch):
    s = auth_routes.get_settings()
    monkeypatch.setattr(s, "google_client_id", "")
    monkeypatch.setattr(s, "google_client_secret", "")


def test_google_start_404_when_unconfigured(monkeypatch):
    _disable_google(monkeypatch)
    with TestClient(app, follow_redirects=False) as c:
        assert c.get("/auth/google").status_code == 404


def test_google_start_redirects_with_state(monkeypatch):
    _enable_google(monkeypatch)
    with TestClient(app, follow_redirects=False) as c:
        r = c.get("/auth/google")
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "state=" in loc and "client_id=test-client-id" in loc


def test_google_login_button_visibility(monkeypatch):
    _disable_google(monkeypatch)
    with TestClient(app) as c:
        assert "Continue with Google" not in c.get("/login").text
    _enable_google(monkeypatch)
    with TestClient(app) as c:
        assert "Continue with Google" in c.get("/login").text


def test_callback_rejects_bad_state(monkeypatch):
    _enable_google(monkeypatch)
    with TestClient(app, follow_redirects=False) as c:
        c.get("/auth/google")   # sets oauth_state in the session
        r = c.get("/auth/google/callback?code=abc&state=forged")
        assert r.status_code == 401
        assert "expired" in r.text


class _FakeResp:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok

    def json(self):
        return self._payload


def _mock_google(monkeypatch, email, verified=True):
    monkeypatch.setattr(auth_routes.requests, "post",
                        lambda *a, **k: _FakeResp({"access_token": "tok"}))
    monkeypatch.setattr(auth_routes.requests, "get",
                        lambda *a, **k: _FakeResp({"email": email,
                                                   "email_verified": verified}))


def _start_and_callback(c, state_from_redirect=True):
    r = c.get("/auth/google")
    state = r.headers["location"].split("state=")[1].split("&")[0]
    return c.get(f"/auth/google/callback?code=fake&state={state}")


def test_callback_owner_email_signs_in(monkeypatch):
    _enable_google(monkeypatch)
    _mock_google(monkeypatch, "dinogcteie@gmail.com")
    with TestClient(app, follow_redirects=False) as c:
        r = _start_and_callback(c)
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        # Session is owner: settings save (admin perm) is permitted.
        assert c.get("/dashboard").status_code == 200


def test_callback_unlisted_email_rejected(monkeypatch):
    _enable_google(monkeypatch)
    _mock_google(monkeypatch, "intruder@example.com")
    with TestClient(app, follow_redirects=False) as c:
        r = _start_and_callback(c)
        assert r.status_code == 403
        assert "not authorized" in r.text
        # And no session was granted.
        assert c.get("/dashboard").status_code == 303


def test_callback_unverified_email_rejected(monkeypatch):
    _enable_google(monkeypatch)
    _mock_google(monkeypatch, "dinogcteie@gmail.com", verified=False)
    with TestClient(app, follow_redirects=False) as c:
        assert _start_and_callback(c).status_code == 401


# ─── Roles settings save ─────────────────────────────────────────────────────

def test_save_roles_normalizes_and_guards_owner_list(client, db):
    r = client.post("/settings/roles", data={
        "role_owners": " A@B.com , a@b.com, junk,  c@d.com ",
        "role_marketing": "Mkt@Studio.in",
    })
    assert r.status_code == 200   # redirected to /settings
    s = db.get_settings_dict()
    assert s["role_owners"] == "a@b.com, c@d.com"
    assert s["role_marketing"] == "mkt@studio.in"

    # Empty owners list is refused (lockout guard) — previous value retained.
    client.post("/settings/roles", data={"role_owners": "  "})
    assert db.get_settings_dict()["role_owners"] == "a@b.com, c@d.com"

    # Restore the seeded defaults for other tests.
    client.post("/settings/roles", data={
        "role_owners": "dinogcteie@gmail.com, lifeinframe.in@gmail.com"})


# ─── Recurring consolidation (cron + Settings button share one core) ────────

def test_settings_button_and_cron_never_double_post(db):
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    cat = db.list_categories(active_only=True)[0]
    tpl = db.create_expense(
        date_=last_month, category_id=cat.id, scope="company",
        payment_status="paid", amount=777.0, paid_amount=777.0,
        paid_to="Dedup Vendor", is_recurring=True, recurring_day=1,
    )
    created = [tpl.id]
    try:
        first = generate_for_month(db, today.year, today.month)
        assert first["posted"] == 1
        created += [e.id for e in db.list_expenses()
                    if f"[auto-recurring #{tpl.id}]" in (e.notes or "")]
        # The other entry point sees the same marker and posts nothing.
        assert post_due_recurring(db)["posted"] == 0
        assert generate_for_month(db, today.year, today.month)["posted"] == 0
    finally:
        for eid in created:
            db.delete_expense(eid)


def test_legacy_auto_generated_rows_block_reposting(db):
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    cat = db.list_categories(active_only=True)[0]
    tpl = db.create_expense(
        date_=last_month, category_id=cat.id, scope="company",
        payment_status="paid", amount=555.0, paid_amount=555.0,
        is_recurring=True, recurring_day=1,
    )
    # Simulate a row created by the OLD Settings-button implementation.
    legacy = db.create_expense(
        date_=today.replace(day=1), category_id=cat.id, scope="company",
        payment_status="pending", amount=555.0,
        notes="[Auto-generated] office rent",
    )
    try:
        assert post_due_recurring(db)["posted"] == 0
        assert generate_for_month(db, today.year, today.month)["posted"] == 0
    finally:
        db.delete_expense(legacy.id)
        db.delete_expense(tpl.id)
