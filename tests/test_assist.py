"""Communication log, AI assists, and client milestone-date tests."""
from datetime import date

from app.services import llm
from app.services.assist import draft_reply, summarize_lead


# ─── Communication log + first_response_at ───────────────────────────────────

def test_comm_log_crud_and_first_response(db):
    lead = db.create_lead(client_name="Comm Log Lead", contact="9876500001")
    assert db.get_lead(lead.id).first_response_at == ""

    log1 = db.create_comm_log("lead", lead.id, channel="call", direction="out",
                              summary="Called, interested")
    fresh = db.get_lead(lead.id)
    assert fresh.first_response_at == log1.created_at      # stamped by first outbound

    db.create_comm_log("lead", lead.id, channel="whatsapp", direction="out",
                       summary="Sent package details")
    assert db.get_lead(lead.id).first_response_at == log1.created_at  # NOT overwritten

    logs = db.list_comm_logs("lead", lead.id)
    assert len(logs) == 2 and logs[0].summary == "Sent package details"  # newest first

    db.delete_comm_log(logs[0].id)
    assert len(db.list_comm_logs("lead", lead.id)) == 1


def test_inbound_touch_does_not_stamp_response(db):
    lead = db.create_lead(client_name="Inbound Only")
    db.create_comm_log("lead", lead.id, channel="whatsapp", direction="in",
                       summary="They messaged us")
    assert db.get_lead(lead.id).first_response_at == ""


def test_comm_log_routes(client, db):
    lead = db.create_lead(client_name="Comm Route Lead")
    r = client.post(f"/leads/{lead.id}/comm",
                    data={"channel": "call", "direction": "out",
                          "summary": "Spoke about wedding package"})
    assert r.status_code == 200
    logs = db.list_comm_logs("lead", lead.id)
    assert len(logs) == 1
    # Empty summary → flash, nothing created
    r = client.post(f"/leads/{lead.id}/comm",
                    data={"channel": "call", "direction": "out", "summary": "  "})
    assert r.status_code == 200
    assert len(db.list_comm_logs("lead", lead.id)) == 1
    # Render shows the touch
    r = client.get(f"/leads/{lead.id}")
    assert "Spoke about wedding package" in r.text


# ─── AI assists ──────────────────────────────────────────────────────────────

def test_draft_reply_prompt_has_context(db, monkeypatch):
    lead = db.create_lead(client_name="Asha", event_type="Wedding",
                          city="Coimbatore", budget_range="1L")
    captured = {}

    def _fake(db_, prompt, system="", max_tokens=0):
        captured["prompt"], captured["system"] = prompt, system
        return "Hi Asha! Thanks for reaching out…"
    monkeypatch.setattr(llm, "complete", _fake)
    out = draft_reply(db, lead, "Life in Frame")
    assert out.startswith("Hi Asha")
    assert "Wedding" in captured["prompt"] and "Coimbatore" in captured["prompt"]
    assert "Life in Frame" in captured["prompt"]
    assert "NEVER invent prices" in captured["system"]


def test_summarize_includes_touches(db, monkeypatch):
    lead = db.create_lead(client_name="Brief Lead", event_type="Engagement")
    db.create_comm_log("lead", lead.id, summary="Quoted 1.5L over call")
    captured = {}

    def _fake(db_, prompt, system="", max_tokens=0):
        captured["prompt"] = prompt
        return "line1\nline2\nline3"
    monkeypatch.setattr(llm, "complete", _fake)
    out = summarize_lead(db, lead, db.list_comm_logs("lead", lead.id))
    assert out.count("\n") == 2
    assert "Quoted 1.5L over call" in captured["prompt"]


def test_draft_route_stores_in_session(client, db, monkeypatch):
    lead = db.create_lead(client_name="Session Draft")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "DRAFT-MARKER-TEXT")
    r = client.post(f"/leads/{lead.id}/draft-reply")
    assert r.status_code == 200
    assert "DRAFT-MARKER-TEXT" in r.text          # rendered on the redirect target
    # Draft is popped — a second GET doesn't show it
    r2 = client.get(f"/leads/{lead.id}")
    assert "DRAFT-MARKER-TEXT" not in r2.text


def test_draft_route_llm_error_flashes(client, db, monkeypatch):
    lead = db.create_lead(client_name="Draft Fail")

    def _boom(*a, **k):
        raise llm.LLMError("quota")
    monkeypatch.setattr(llm, "complete", _boom)
    r = client.post(f"/leads/{lead.id}/draft-reply")
    assert r.status_code == 200                    # flash + redirect, no 500
    assert "Couldn" in r.text


# ─── Client milestone dates + dashboard card ─────────────────────────────────

def test_client_dates_roundtrip(client, db):
    r = client.post("/clients", data={
        "name": "Milestone Client", "phone": "9876512345",
        "birthday": "1990-05-20", "anniversary": "2020-12-01",
    })
    assert r.status_code == 200
    c = next(c for c in db.list_clients() if c.name == "Milestone Client")
    assert c.birthday == date(1990, 5, 20)
    assert c.anniversary == date(2020, 12, 1)


def test_dashboard_campaign_opportunities(client, db):
    today = date.today()
    db.create_client(name="This Month B'day", phone="9876509999",
                     birthday=date(1992, today.month, min(today.day, 28)))
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Campaign opportunities" in r.text
    assert "This Month B&#39;day" in r.text or "This Month B'day" in r.text
