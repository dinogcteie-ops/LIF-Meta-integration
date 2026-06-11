"""Delivery dashboard & milestone tests.

Tests are numbered to match the plan spec:
 1. Milestone seeding on event create
 2. Mapping fn: empty list → None
 3. Mapping: Shoot complete → shooting_done
 4. Mapping: Culling done → editing
 5. Mapping: Client review done → review
 6. Mapping: all complete → delivered
 7. Toggle via db + sync updates event.delivery_status
 8. GET /delivery returns 200 with seeded data
 9. Overdue flagging: past-due incomplete milestone → "Overdue" in /delivery response
10. Portal renders milestone phase names when milestones exist
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain import Milestone
from app.services.db import Database
from app.services.delivery import delivery_status_from_milestones


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_milestone(phase: str, position: int, completed: bool = False) -> Milestone:
    """Build a standalone Milestone (no DB) for mapping-fn tests."""
    return Milestone(
        id=position,
        event_id=0,
        phase=phase,
        position=position,
        completed_at=date.today() if completed else None,
    )


# ─── 1. Milestone seeding on event create ─────────────────────────────────────

def test_seed_milestones_on_event_create(db: Database):
    ev = db.create_event(name="Seeding Test", event_type="Wedding")
    db.seed_milestones(ev.id, "Wedding")
    milestones = db.list_milestones(event_id=ev.id)
    # Wedding → default 6-phase list
    assert len(milestones) == 6
    phases = [m.phase for m in milestones]
    assert "Shoot" in phases
    assert "Final delivery" in phases
    # positions are sequential
    assert [m.position for m in milestones] == list(range(6))


# ─── 2. Mapping fn: empty list → None ────────────────────────────────────────

def test_mapping_empty_list_returns_none():
    assert delivery_status_from_milestones([]) is None


# ─── 3. Mapping: Shoot complete → shooting_done ───────────────────────────────

def test_mapping_shoot_complete():
    ms = [
        _make_milestone("Shoot", 0, completed=True),
        _make_milestone("Culling", 1, completed=False),
        _make_milestone("Editing", 2, completed=False),
        _make_milestone("Final delivery", 3, completed=False),
    ]
    assert delivery_status_from_milestones(ms) == "shooting_done"


# ─── 4. Mapping: Culling done → editing ──────────────────────────────────────

def test_mapping_culling_complete():
    ms = [
        _make_milestone("Shoot", 0, completed=True),
        _make_milestone("Culling", 1, completed=True),
        _make_milestone("Editing", 2, completed=False),
        _make_milestone("Final delivery", 3, completed=False),
    ]
    assert delivery_status_from_milestones(ms) == "editing"


# ─── 5. Mapping: Client review done → review ──────────────────────────────────

def test_mapping_client_review_complete():
    ms = [
        _make_milestone("Shoot", 0, completed=True),
        _make_milestone("Culling", 1, completed=True),
        _make_milestone("Editing", 2, completed=True),
        _make_milestone("Client review", 3, completed=True),
        _make_milestone("Album design", 4, completed=False),
        _make_milestone("Final delivery", 5, completed=False),
    ]
    assert delivery_status_from_milestones(ms) == "review"


# ─── 6. Mapping: all complete → delivered ────────────────────────────────────

def test_mapping_all_complete():
    ms = [
        _make_milestone("Shoot", 0, completed=True),
        _make_milestone("Culling", 1, completed=True),
        _make_milestone("Editing", 2, completed=True),
        _make_milestone("Client review", 3, completed=True),
        _make_milestone("Album design", 4, completed=True),
        _make_milestone("Final delivery", 5, completed=True),
    ]
    assert delivery_status_from_milestones(ms) == "delivered"


# ─── 7. Toggle + sync updates delivery_status ─────────────────────────────────

def test_toggle_and_sync(db: Database):
    ev = db.create_event(name="Toggle Test")
    db.seed_milestones(ev.id, None)
    milestones = db.list_milestones(event_id=ev.id)
    assert milestones, "Expected seeded milestones"

    # Initially none are complete — status should be None
    db.sync_delivery_status(ev.id)
    ev_fresh = db.get_event(ev.id)
    assert ev_fresh.delivery_status is None

    # Toggle the Shoot milestone (position 0) complete
    shoot = next(m for m in milestones if m.phase == "Shoot")
    db.toggle_milestone(shoot.id)
    db.sync_delivery_status(ev.id)

    ev_after = db.get_event(ev.id)
    assert ev_after.delivery_status == "shooting_done"

    # Toggle back — status returns to None
    db.toggle_milestone(shoot.id)
    db.sync_delivery_status(ev.id)
    ev_back = db.get_event(ev.id)
    assert ev_back.delivery_status is None


# ─── 8. GET /delivery returns 200 ────────────────────────────────────────────

def test_delivery_page_200(client: TestClient, db: Database):
    ev = db.create_event(name="Delivery Dash Event", status="active")
    db.seed_milestones(ev.id, None)
    r = client.get("/delivery")
    assert r.status_code == 200
    assert "Delivery" in r.text


# ─── 9. Overdue milestone appears in /delivery response ──────────────────────

def test_overdue_milestone_appears_in_delivery(client: TestClient, db: Database):
    yesterday = date.today() - timedelta(days=1)
    ev = db.create_event(name="Overdue Event", status="active")
    db.seed_milestones(ev.id, None)
    milestones = db.list_milestones(event_id=ev.id)
    # Set the Shoot milestone's due_date to yesterday (overdue, not complete)
    shoot = next(m for m in milestones if m.phase == "Shoot")
    db.update_milestone(shoot.id, due_date=yesterday)

    r = client.get("/delivery")
    assert r.status_code == 200
    assert "Overdue" in r.text


# ─── 10. Portal renders milestone phase names when milestones exist ───────────

def test_portal_renders_milestones(client: TestClient, db: Database):
    from app.routes.portal import generate_portal_url
    ev = db.create_event(name="Portal Milestone Event", status="active")
    db.seed_milestones(ev.id, "Wedding")

    url = generate_portal_url(ev.id)
    # Portal is public — use a plain (unauthenticated) client
    from fastapi.testclient import TestClient as _TC
    from app.main import app
    with _TC(app, follow_redirects=True) as public:
        r = public.get(url)
    assert r.status_code == 200
    # The milestone phase names should appear
    assert "Shoot" in r.text
    assert "Final delivery" in r.text
