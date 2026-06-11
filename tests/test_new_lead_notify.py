"""Tests for the resilient, cursor-based new-lead owner notification.

The session DB persists rows across tests, so each test first pins the notify
cursor to the current max lead id (isolating it from leads other tests created),
then adds its own leads with a distinct phone range.
"""
import app.services.reminders as reminders

_CURSOR_KEY = "new_lead_notify_cursor"


def _pin_cursor_to_now(db) -> int:
    """Set the notify cursor to the current max lead id; return it."""
    max_id = max((l.id for l in db.list_leads()), default=0)
    db.set_settings({_CURSOR_KEY: str(max_id)})
    return max_id


def _patch_email(monkeypatch, sink: list):
    monkeypatch.setattr(reminders, "email_configured", lambda: True)
    monkeypatch.setattr(reminders, "send_email",
                        lambda subject, html, recipients, text=None:
                        sink.append((subject, html, recipients)))


def test_first_run_initialises_cursor_without_sending(db, monkeypatch):
    db.set_settings({_CURSOR_KEY: ""})            # force the first-run branch
    sent: list = []
    _patch_email(monkeypatch, sent)

    res = reminders.notify_new_leads(db)

    assert res["notified"] is False
    assert res["reason"] == "cursor_initialised"
    assert sent == []                              # historical leads never blasted


def test_sends_for_new_inbound_lead_and_advances_cursor(db, monkeypatch):
    _pin_cursor_to_now(db)
    lead = db.create_lead(client_name="Notify One", contact="+919900000001",
                          event_type="Wedding", source="Instagram",
                          meta_campaign=True, meta_campaign_name="IG Test")
    sent: list = []
    _patch_email(monkeypatch, sent)

    res = reminders.notify_new_leads(db)

    assert res["notified"] is True
    assert res["leads"] == 1
    assert len(sent) == 1
    subject, html, recipients = sent[0]
    assert "Notify One" in html
    assert f"/leads/{lead.id}" in html             # links straight to the lead
    assert recipients                              # owners from role_owners
    assert int(db.get_settings_dict()[_CURSOR_KEY]) >= lead.id

    # A second run with nothing new sends nothing (idempotent).
    res2 = reminders.notify_new_leads(db)
    assert res2["notified"] is False
    assert res2["reason"] == "no_new_leads"
    assert len(sent) == 1


def test_send_failure_keeps_cursor_and_retries(db, monkeypatch):
    cursor_before = _pin_cursor_to_now(db)
    db.create_lead(client_name="Retry Lead", contact="+919900000002",
                   source="Meta", meta_campaign=True)

    monkeypatch.setattr(reminders, "email_configured", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(reminders, "send_email", boom)

    res = reminders.notify_new_leads(db)
    assert res["notified"] is False
    assert "smtp down" in res["reason"]
    # Cursor must NOT advance on failure, so the lead is retried.
    assert int(db.get_settings_dict()[_CURSOR_KEY]) == cursor_before

    # Now the send succeeds → the same lead is picked up on retry.
    sent: list = []
    _patch_email(monkeypatch, sent)
    res2 = reminders.notify_new_leads(db)
    assert res2["notified"] is True
    assert res2["leads"] >= 1
    assert "Retry Lead" in sent[0][1]
    assert int(db.get_settings_dict()[_CURSOR_KEY]) > cursor_before


def test_manual_non_inbound_lead_is_not_notified(db, monkeypatch):
    _pin_cursor_to_now(db)
    db.create_lead(client_name="Walk In", contact="+919900000003",
                   source="Referral", meta_campaign=False)
    sent: list = []
    _patch_email(monkeypatch, sent)

    res = reminders.notify_new_leads(db)

    assert res["notified"] is False
    assert res["reason"] == "no_new_leads"
    assert sent == []


def test_deferred_when_no_recipients_configured(db, monkeypatch):
    _pin_cursor_to_now(db)
    db.create_lead(client_name="No Owner Lead", contact="+919900000004",
                   source="Instagram", meta_campaign=True)
    sent: list = []
    _patch_email(monkeypatch, sent)
    saved_owners = db.get_settings_dict().get("role_owners", "")
    db.set_settings({"role_owners": ""})
    try:
        res = reminders.notify_new_leads(db)
        assert res["notified"] is False
        assert res["reason"] == "no_recipients"
        assert sent == []
        # Cursor not advanced → leads still pending once owners are set.
    finally:
        db.set_settings({"role_owners": saved_owners})
