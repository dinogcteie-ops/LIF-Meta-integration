"""LLM lead-triage tests — fake transport, no network."""
import pytest

from app.services import llm, triage
from app.services.reminders import build_new_leads_email


def _mk_lead(db, name="Triage Test", **kw):
    return db.create_lead(client_name=name, **kw)


# ─── triage_lead happy path ───────────────────────────────────────────────────

def test_triage_lead_persists_verdict(db, monkeypatch):
    lead = _mk_lead(db, contact="+91 98888 77777", event_type="Wedding",
                    budget_range="75,000", city="Chennai")
    monkeypatch.setattr(llm, "complete_json",
                        lambda *a, **k: {"triage": "hot", "reason": "Clear wedding, budget stated"})
    verdict = triage.triage_lead(db, lead)
    assert verdict == "hot"
    fresh = db.get_lead(lead.id)
    assert fresh.triage == "hot"
    assert fresh.triage_source == "llm"
    assert "budget" in fresh.triage_reason.lower()
    assert fresh.triaged_at


def test_triage_lead_rejects_unknown_class(db, monkeypatch):
    lead = _mk_lead(db, name="Bad Class")
    monkeypatch.setattr(llm, "complete_json",
                        lambda *a, **k: {"triage": "lukewarm", "reason": "?"})
    with pytest.raises(llm.LLMError):
        triage.triage_lead(db, lead)
    assert db.get_lead(lead.id).triage == ""   # untouched


# ─── triage_pending_leads (batch, cron path) ─────────────────────────────────

def test_pending_skips_when_unconfigured(db, monkeypatch):
    # Force the no-key path explicitly (a local .env must not change the result).
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    out = triage.triage_pending_leads(db)
    assert out["skipped"] == "llm not configured"


def test_pending_batch_failure_leaves_lead_untriaged(db, monkeypatch):
    lead = _mk_lead(db, name="LLM Down")
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    def _boom(*a, **k):
        raise llm.LLMError("provider down")
    monkeypatch.setattr(llm, "complete_json", _boom)
    out = triage.triage_pending_leads(db, limit=5)
    assert out["failed"] >= 1 and out["triaged"] == 0
    assert db.get_lead(lead.id).triage == ""   # retried next tick


def test_pending_batch_triages_all(db, monkeypatch):
    l1 = _mk_lead(db, name="Batch One")
    l2 = _mk_lead(db, name="Batch Two")
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete_json",
                        lambda *a, **k: {"triage": "warm", "reason": "ok"})
    out = triage.triage_pending_leads(db, limit=50)
    assert out["triaged"] >= 2 and out["failed"] == 0
    assert db.get_lead(l1.id).triage == "warm"
    assert db.get_lead(l2.id).triage == "warm"


# ─── Budget counter ───────────────────────────────────────────────────────────

def test_daily_budget_blocks_calls(db):
    from datetime import date
    key = f"llm_calls_{date.today().isoformat()}"
    db.set_settings({key: "999999"})
    with pytest.raises(llm.LLMError, match="budget"):
        llm.complete(db, "hi")
    db.set_settings({key: ""})   # cleanup for other tests


def test_budget_counter_increments(db, monkeypatch):
    from datetime import date

    def _raise(*a, **k):
        raise llm.LLMError("no network in tests")
    # Force the provider call to fail so we test counting independent of any key.
    monkeypatch.setattr(llm, "_complete_gemini", _raise)
    monkeypatch.setattr(llm, "_complete_anthropic", _raise)
    key = f"llm_calls_{date.today().isoformat()}"
    db.set_settings({key: "0"})
    with pytest.raises(llm.LLMError):
        llm.complete(db, "hi")
    assert int(db.get_settings_dict().get(key) or 0) == 1   # counted before the call
    db.set_settings({key: ""})


# ─── complete_json fence tolerance ───────────────────────────────────────────

def test_complete_json_strips_fences(db, monkeypatch):
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: '```json\n{"triage": "spam", "reason": "bot"}\n```')
    out = llm.complete_json(db, "x")
    assert out == {"triage": "spam", "reason": "bot"}


def test_complete_json_rejects_non_json(db, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "sorry, I cannot")
    with pytest.raises(llm.LLMError):
        llm.complete_json(db, "x")


# ─── Manual override route ───────────────────────────────────────────────────

def test_manual_override_route(client, db):
    lead = _mk_lead(db, name="Manual Override")
    r = client.post(f"/leads/{lead.id}/triage", data={"triage": "hot"})
    assert r.status_code == 200
    fresh = db.get_lead(lead.id)
    assert fresh.triage == "hot" and fresh.triage_source == "manual"


def test_manual_override_bad_class(client, db):
    lead = _mk_lead(db, name="Bad Override")
    r = client.post(f"/leads/{lead.id}/triage", data={"triage": "nope"})
    assert r.status_code == 200
    assert db.get_lead(lead.id).triage == ""


# ─── New-lead email carries the tag ──────────────────────────────────────────

def test_new_lead_email_includes_hot_tag(db):
    lead = _mk_lead(db, name="Hot Email Lead", source="Instagram")
    db.set_lead_triage(lead.id, "hot", "llm", "test")
    fresh = db.get_lead(lead.id)
    subject, html = build_new_leads_email([fresh], "https://example.com")
    assert "hot" in subject.lower()
    assert "Hot" in html
