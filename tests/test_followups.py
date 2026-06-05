"""Tests for follow-up reminder selection and digest rendering."""
from datetime import date

from app.services.reminders import build_followup_digest, due_followups

TODAY = date(2026, 6, 15)


def test_due_followups_only_active_due_today(db):
    # Should be picked up: New + Quoted, due today, not done.
    a = db.create_lead(client_name="Due New", status="new",
                       followup_status="pending", followup_date=TODAY)
    b = db.create_lead(client_name="Due Quoted", status="quoted",
                       followup_status="scheduled", followup_date=TODAY)
    # Should be excluded:
    db.create_lead(client_name="Won Today", status="won",
                   followup_date=TODAY)                       # wrong status
    db.create_lead(client_name="Lost Today", status="lost",
                   followup_date=TODAY)                       # wrong status
    db.create_lead(client_name="Done Today", status="new",
                   followup_status="done", followup_date=TODAY)  # already done
    db.create_lead(client_name="Future", status="new",
                   followup_date=date(2026, 7, 1))            # not today
    db.create_lead(client_name="No Date", status="new")        # no follow-up date

    due = due_followups(db, TODAY)
    names = {l.client_name for l in due}
    assert names == {"Due New", "Due Quoted"}
    assert {l.id for l in due} == {a.id, b.id}


def test_digest_contains_links_and_count(db):
    leads = due_followups(db, TODAY)
    subject, html = build_followup_digest(leads, "https://example.test/", TODAY)
    assert str(len(leads)) in subject
    for l in leads:
        assert f"https://example.test/leads/{l.id}" in html
    assert "Follow-ups due today" in html
