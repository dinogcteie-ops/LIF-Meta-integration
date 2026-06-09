"""Lead workflow rules: lost -> follow-up done, lost requires a reason, and the
lost-leads source/date filter."""
from datetime import date

from app.domain import Lead
from app.services.reports import filter_lost_leads


# ── #1 lost forces follow-up status = done ────────────────────────────────────

def test_create_lost_forces_followup_done(db):
    lead = db.create_lead(client_name="CreateLost", status="lost",
                          rejection_reason="Chose Competitor",
                          followup_status="pending")
    assert lead.status == "lost"
    assert lead.followup_status == "done"


def test_update_to_lost_forces_followup_done(db):
    lead = db.create_lead(client_name="UpdLost", status="new",
                          followup_status="pending")
    updated = db.update_lead(lead.id, client_name="UpdLost", status="lost",
                             rejection_reason="Budget / Price Mismatch",
                             followup_status="scheduled")
    assert updated.status == "lost"
    assert updated.followup_status == "done"   # forced regardless of submitted value


# ── #2 lost requires a reason (route-level) ───────────────────────────────────

def test_lost_without_reason_is_blocked(client, db):
    client.post("/leads", data={"client_name": "NoReasonBlock", "status": "lost"})
    assert [l for l in db.list_leads() if l.client_name == "NoReasonBlock"] == []


def test_lost_with_reason_creates(client, db):
    client.post("/leads", data={"client_name": "WithReason", "status": "lost",
                                "rejection_reason": "Chose Competitor"})
    made = [l for l in db.list_leads() if l.client_name == "WithReason"]
    assert len(made) == 1
    assert made[0].status == "lost"
    assert made[0].followup_status == "done"
    assert made[0].rejection_reason == "Chose Competitor"


# ── #4 lost-leads source + enquiry-date filter ────────────────────────────────

def _mk(i, status, source, created):
    return Lead(id=i, client_name=f"L{i}", status=status, source=source, created_at=created)


def test_filter_lost_leads_by_source_and_date():
    leads = [
        _mk(1, "lost", "Instagram", "2026-01-10T09:00:00"),
        _mk(2, "lost", "Referral",  "2026-03-15T09:00:00"),
        _mk(3, "lost", "Instagram", "2026-06-01T09:00:00"),
        _mk(4, "won",  "Instagram", "2026-06-01T09:00:00"),   # not lost
        _mk(5, "lost", "Instagram", ""),                       # no parseable date
    ]
    assert {l.id for l in filter_lost_leads(leads)} == {1, 2, 3, 5}
    assert {l.id for l in filter_lost_leads(leads, source="Instagram")} == {1, 3, 5}
    # Date range excludes out-of-range and undated leads.
    got = filter_lost_leads(leads, source="Instagram",
                            start=date(2026, 1, 1), end=date(2026, 3, 31))
    assert {l.id for l in got} == {1}
    got_all = filter_lost_leads(leads, source="all",
                                start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert {l.id for l in got_all} == {1, 2, 3}   # id 5 dropped (no date)
